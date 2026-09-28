"""Virtual time for asyncio without replacing asyncio.

Design (after the independent trust audit): keep every native asyncio primitive
(``sleep``, ``wait_for``, ``timeout``, ``wait``, ``call_later``, ``call_at``) and
virtualise the two things they depend on:

1. **The loop's time source.** While a :class:`VirtualClock` is installed,
   ``BaseEventLoop.time()`` returns virtual monotonic seconds, so every timer
   asyncio creates is scheduled on virtual time with its exact native semantics.
2. **Waiting.** A loop that has nothing runnable calls ``selector.select(timeout)``
   with the delay to its next timer. The installed selector proxy turns that
   call into a *park*: standalone (no engine) it jumps the clock to the timer
   and returns at once (Trio's autojump); under an engine it registers the park
   (loop, deadline, owning execution) and blocks on the real selector until the
   engine advances the clock and wakes exactly that loop. A loop waiting with no
   timer (only external futures) parks with no deadline.

Timers are due at whole microseconds (the datetime's resolution; the contract is
docs/contracts.md). ``call_later`` and ``call_at`` convert
a request once, when it is registered, to the loop time of its due instant: a
delay is due at ``now + timedelta(seconds=delay)``; an absolute loop time at the
microsecond nearest to it (ties to even). The loop's clock resolution is half a
microsecond, so asyncio's own due test means exactly "due instant <= now" and
nothing else in the program moves a timer's due instant. That holds while the
float ``loop.time()`` can resolve microseconds: up to and including
``MAX_ELAPSED_US`` (about 34 years) after the clock start, where ``loop.time()`` is
exactly 2**30. The clock refuses to advance beyond it (:class:`ClockRangeError`)
rather than fire timers on a coarser grid; scenario entry points check a horizon
against the same bound (:func:`range_error`) before anything runs.

Equal-deadline wake-ups therefore happen one loop at a time in park order, and
cancelling a timer simply removes it from asyncio's own heap. Nothing here
re-implements asyncio semantics.

The :class:`VirtualClock` is also installed into the an explicitly supplied clock seam and
mirrored into ``time_machine`` so code reading ``datetime.now``/``time.time``
directly agrees with the loops.
"""
from __future__ import annotations

import asyncio
import contextvars
import heapq
import os
import selectors
import sysconfig
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Callable, Optional

import time_machine

from workflow_sim import time as default_seam, runtime as default_bridge

#: The execution (engine WorkItem) the current thread/coroutine belongs to.
current_item: contextvars.ContextVar[Any] = contextvars.ContextVar("sim_current_item", default=None)

_real_loop_time = asyncio.BaseEventLoop.time
_real_call_later = asyncio.BaseEventLoop.call_later
_real_call_at = asyncio.BaseEventLoop.call_at
_real_call_soon_threadsafe = asyncio.BaseEventLoop.call_soon_threadsafe
_real_loop_close = asyncio.BaseEventLoop.close
_real_selector_init = asyncio.selector_events.BaseSelectorEventLoop.__init__
_ACTIVE: list["VirtualClock"] = []     # installed clocks (innermost last)
_US = timedelta(microseconds=1)
_LOOP_EPOCH = 1_000_000                # loop.time() at the clock start
# The supported elapsed range, inclusive: loop.time() up to and including 2**30.
# There the float spacing of loop.time() is at most 2**-23 s (1.2e-7), so each whole microsecond has its own loop time, float
# error stays far below the half-microsecond due tolerance, and a select timeout
# converts back to the exact microsecond count. That holds up to about 2**31; the
# bound keeps a factor of two. At 2**33 the tolerance no longer moves the float
# and a 1us timer livelocks. About 34 years of virtual time.
MAX_ELAPSED_US = (2**30 - _LOOP_EPOCH) * 1_000_000


class ClockRangeError(RuntimeError):
    """The clock was asked to move beyond the supported elapsed range."""


def range_error(start: datetime, when: datetime) -> Optional[str]:
    """Why a clock started at ``start`` cannot reach ``when``, or None if it can.
    ``start + MAX_ELAPSED_US`` itself is supported; one microsecond later is not."""
    elapsed = _aware(when) - _aware(start)
    if elapsed // _US <= MAX_ELAPSED_US:
        return None
    return (f"virtual time {_aware(when).isoformat()} is {elapsed} after the clock start; "
            f"microsecond timers are supported up to {timedelta(microseconds=MAX_ELAPSED_US)}")


def _loop_time(us: int) -> float:
    """The loop time of whole microsecond ``us`` since the clock start."""
    return us / 1_000_000 + float(_LOOP_EPOCH)


class SimulatedWorkerCrash(BaseException):
    """Raised inside an execution when the scenario kills its worker gracefully
    (``cancel_execution``). A hard crash never raises anything: the execution is
    abandoned and fenced. ``BaseException`` so ``except Exception`` cannot eat it."""


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(order=True)
class Park:
    """One event loop waiting inside select()."""
    sort_key: tuple
    seq: int
    loop: Any = field(compare=False, repr=False)
    when: Optional[datetime] = field(compare=False, default=None)   # next timer, None = external only
    item: Any = field(compare=False, repr=False, default=None)
    thread_id: int = field(compare=False, default=0)
    woken: bool = field(compare=False, default=False)


class Scheduler:
    """Thread-safe registry of parked loops, shared by every loop in the process."""

    def __init__(self):
        self.cv = _FenceSafeCondition()
        self.parks: list[Park] = []
        self.seq = 0
        self.engine = None
        # Cross-thread callbacks posted with ``call_soon_threadsafe`` that the
        # target loop has not started running yet. A loop with pending posts is
        # not parked even while its thread still sits in ``select``: the
        # self-pipe wake is in flight.
        self.pending: dict[int, int] = {}
        # Timer callbacks a loop discarded by closing before they were due
        # (``asyncio.run`` returning with a ``call_later`` still scheduled).
        self.dropped_timers: list[dict] = []
        # Threads waiting in ``run_coro_sync`` (any execution or none), by thread
        # id, and the loop each one waits on.
        self.blocked_threads: dict[int, int] = {}
        self.blocked_on: dict[int, Any] = {}

    def post(self, loop) -> None:
        with self.cv:
            self.pending[id(loop)] = self.pending.get(id(loop), 0) + 1

    def posted_ran(self, loop) -> None:
        with self.cv:
            n = self.pending.get(id(loop), 0) - 1
            if n <= 0:
                self.pending.pop(id(loop), None)
            else:
                self.pending[id(loop)] = n
            self.cv.notify_all()

    def pending_for(self, loop) -> int:
        with self.cv:
            return self.pending.get(id(loop), 0)

    def park(self, loop, when: Optional[datetime], item, thread_id: int) -> Park:
        with self.cv:
            self.seq += 1
            key = (0, when) if when is not None else (1, 0.0)
            p = Park(key, self.seq, loop, when, item, thread_id)
            self.parks.append(p)
            self.cv.notify_all()
        return p

    def unpark(self, p: Park) -> None:
        with self.cv:
            if p in self.parks:
                self.parks.remove(p)
            self.cv.notify_all()

    def earliest(self) -> Optional[Park]:
        with self.cv:
            timed = [p for p in self.parks if p.when is not None and not p.woken]
            return min(timed) if timed else None

    def parks_for(self, item) -> list[Park]:
        with self.cv:
            return [p for p in self.parks if p.item is item]

    def wake(self, p: Park) -> None:
        """Wake one parked loop (after the clock was advanced to its deadline)."""
        with self.cv:
            p.woken = True
        try:
            p.loop.call_soon_threadsafe(_noop)
        except RuntimeError:
            pass

    def forget(self, item) -> int:
        """Drop the parks of an abandoned execution: those loops are never woken."""
        with self.cv:
            mine = [p for p in self.parks if p.item is item]
            for p in mine:
                self.parks.remove(p)
            self.cv.notify_all()
        return len(mine)

    def block_thread(self, tid: int, delta: int, loop: Any = None) -> None:
        with self.cv:
            n = self.blocked_threads.get(tid, 0) + delta
            if n <= 0:
                self.blocked_threads.pop(tid, None)
                self.blocked_on.pop(tid, None)
            else:
                self.blocked_threads[tid] = n
                if loop is not None:
                    self.blocked_on[tid] = loop
            self.cv.notify_all()

    def thread_waiting(self, tid: Optional[int]) -> bool:
        """``tid`` can make no progress until the engine acts: it is blocked in
        ``run_coro_sync`` on a loop parked in ``select`` with no wake pending, or
        drives such a loop itself. A blocked thread whose loop is running (it
        left ``select`` and may be about to release it) is not waiting."""
        with self.cv:
            if self.blocked_threads.get(tid, 0) > 0:
                loop = self.blocked_on.get(tid)
                return loop is None or self._loop_parked(loop)
            return any(p.thread_id == tid and not p.woken and not self.pending.get(id(p.loop))
                       for p in self.parks)

    def _loop_parked(self, loop) -> bool:
        return not self.pending.get(id(loop)) and any(p.loop is loop and not p.woken for p in self.parks)

    def notify(self) -> None:
        with self.cv:
            self.cv.notify_all()


def _noop():
    pass


class _VirtualSelector:
    """Selector proxy: a blocking ``select(timeout)`` becomes a park."""

    def __init__(self, real, loop, clock: "VirtualClock"):
        self._real = real
        self._loop = loop
        self._clock = clock

    def __getattr__(self, name):
        return getattr(self._real, name)

    def select(self, timeout=None):
        clock = self._clock
        if not clock.installed or timeout is not None and timeout <= 0:
            return self._real.select(timeout)
        sched = clock.scheduler
        # Every timer is due at a whole microsecond (see ``call_later``/``call_at``
        # in ``install``), so the timeout is a whole number of microseconds up to
        # float error and this recovers the timer's due instant exactly.
        when = clock.now() + timedelta(seconds=timeout) if timeout is not None else None
        if sched.engine is None:
            # Standalone autojump: nothing is runnable, so time moves to the next timer.
            events = self._real.select(0)
            if events or when is None:
                return events if events else self._real.select(None) if when is None else events
            clock.advance_to(when, reason="autojump")
            return []
        item = current_item.get(None) or getattr(self._loop, "_sim_item", None)
        owner = getattr(self._loop, "_sim_item", None)
        if when is None and owner is not None:
            # A private loop whose execution finished with nothing scheduled and
            # no pool call left to deliver would park forever: retire it instead
            # (its thread exits). Until then the execution is not settled: this
            # loop may still run what the body left scheduled.
            with sched.cv:
                retire = owner.finished and not owner.abandoned and not owner.executor_calls
                if retire:
                    owner.loop_retired = True
                    sched.cv.notify_all()
            if retire:
                self._loop.stop()
                return []
        p = sched.park(self._loop, when, item, threading.get_ident())
        try:
            events = self._real.select(None)
        finally:
            sched.unpark(p)
        if owner is not None and owner.abandoned:
            _freeze_forever()          # guarded frame: an abandoned loop never continues
        return events


class VirtualClock:
    """Monotone virtual time. ``advance_to`` never moves backwards."""

    def __init__(self, start: datetime, *, clock_seam=None, asyncio_bridge=None):
        self.seam = clock_seam or default_seam
        self.asyncio_bridge = asyncio_bridge or default_bridge
        self._start = _aware(start)
        self._now = self._start
        self._traveller: Optional[time_machine.travel] = None
        self._coordinates = None
        self._listeners: list[Callable[[datetime], None]] = []
        self.jumps: list[tuple[datetime, datetime, str]] = []
        self.scheduler = Scheduler()
        self._lock = threading.Lock()
        self.installed = False
        self._wrapped_loops: list[tuple[Any, Any]] = []

    # ClockProvider protocol -------------------------------------------------
    def now(self) -> datetime:
        return self._now

    def time(self) -> float:
        return self._now.timestamp()

    def monotonic(self) -> float:
        return (self._now - self._start).total_seconds()

    def elapsed_us(self) -> int:
        return (self._now - self._start) // _US

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)   # native; virtual through loop.time()

    # Control ----------------------------------------------------------------
    @property
    def start(self) -> datetime:
        return self._start

    def elapsed(self) -> timedelta:
        return self._now - self._start

    def on_advance(self, fn: Callable[[datetime], None]) -> None:
        self._listeners.append(fn)

    def advance_to(self, when: datetime, *, reason: str = "advance") -> None:
        when = _aware(when)
        with self._lock:
            if when <= self._now:
                return
            beyond = range_error(self._start, when)
            if beyond is not None:
                raise ClockRangeError(beyond)
            before = self._now
            self._now = when
            if self._coordinates is not None:
                self._coordinates.move_to(when)
            self.jumps.append((before, when, reason))
        for fn in self._listeners:
            fn(when)

    def advance_by(self, delta: timedelta, *, reason: str = "advance") -> None:
        self.advance_to(self._now + delta, reason=reason)

    # Installation -----------------------------------------------------------
    def wrap_loop(self, loop) -> None:
        """Route ``loop``'s waiting through the park mechanism."""
        real = getattr(loop, "_selector", None)
        if real is None or isinstance(real, _VirtualSelector):
            return
        loop._selector = _VirtualSelector(real, loop, self)
        # Timer deadlines are whole microseconds (``install``): with half a
        # microsecond, asyncio's test ``when < time() + resolution`` holds exactly
        # for the timers due at or before now, within ``MAX_ELAPSED_US``.
        loop._sim_real_resolution = getattr(loop, "_clock_resolution", None)
        loop._clock_resolution = 5e-7
        self._wrapped_loops.append((loop, real))

    def install(self) -> "VirtualClock":
        if self.installed:
            return self
        if self.seam is not None:
            self.seam.install(self)
        self._traveller = time_machine.travel(self._now, tick=False)
        self._coordinates = self._traveller.start()
        clock = self
        if not _ACTIVE:
            def _time(loop_self):
                active = _ACTIVE[-1] if _ACTIVE else None
                return _loop_time(active.elapsed_us()) if active else _real_loop_time(loop_self)

            # A timer is converted once, when it is registered, to the loop time
            # of the whole microsecond it is due at. A request that cannot be
            # represented (inf, nan, not a number, beyond the timedelta range or
            # ``MAX_ELAPSED_US``) keeps asyncio's native deadline; it can never be
            # due, because the clock refuses to advance past the range.
            def _call_later(loop_self, delay, callback, *args, context=None):
                active = _ACTIVE[-1] if _ACTIVE else None
                if active is not None:
                    try:
                        due = active.elapsed_us() + timedelta(seconds=delay) // _US
                    except (OverflowError, ValueError, TypeError):
                        due = None
                    if due is not None and due <= MAX_ELAPSED_US:
                        when = _loop_time(due)
                        timer = _real_call_at(loop_self, when, callback, *args, context=context)
                        if timer._source_traceback:
                            del timer._source_traceback[-1]
                        return timer
                return _real_call_later(loop_self, delay, callback, *args, context=context)

            def _call_at(loop_self, when, callback, *args, context=None):
                if _ACTIVE:
                    try:
                        due = round((Fraction(when) - _LOOP_EPOCH) * 1_000_000)
                    except (OverflowError, ValueError, TypeError):
                        due = None
                    if due is not None and due <= MAX_ELAPSED_US:
                        when = _loop_time(due)
                timer = _real_call_at(loop_self, when, callback, *args, context=context)
                if timer._source_traceback:
                    del timer._source_traceback[-1]
                return timer

            def _init(loop_self, *args, **kwargs):
                _real_selector_init(loop_self, *args, **kwargs)
                active = _ACTIVE[-1] if _ACTIVE else None
                if active is not None:
                    active.wrap_loop(loop_self)

            def _call_soon_threadsafe(loop_self, callback, *args, context=None):
                active = _ACTIVE[-1] if _ACTIVE else None
                sched = active.scheduler if active is not None else None
                if sched is None or sched.engine is None:
                    return _real_call_soon_threadsafe(loop_self, callback, *args, context=context)
                sched.post(loop_self)

                def _run():
                    sched.posted_ran(loop_self)
                    callback(*args)

                _run.__qualname__ = getattr(callback, "__qualname__", repr(callback))
                return _real_call_soon_threadsafe(loop_self, _run, context=context)

            def _close(loop_self):
                active = _ACTIVE[-1] if _ACTIVE else None
                sched = active.scheduler if active is not None else None
                if sched is not None and sched.engine is not None:
                    live = [h for h in getattr(loop_self, "_scheduled", ()) if not h.cancelled()]
                    if live:
                        item = getattr(loop_self, "_sim_item", None) or current_item.get(None)
                        for h in live:
                            cb = getattr(h, "_callback", None)
                            sched.dropped_timers.append({
                                "execution": getattr(item, "id", None), "label": getattr(item, "label", None),
                                "callback": getattr(cb, "__qualname__", repr(cb)),
                                "due_in_seconds": round(h.when() - loop_self.time(), 6)})
                        sched.engine.on_timers_dropped(item, live)
                return _real_loop_close(loop_self)

            asyncio.BaseEventLoop.time = _time
            asyncio.BaseEventLoop.call_later = _call_later
            asyncio.BaseEventLoop.call_at = _call_at
            asyncio.selector_events.BaseSelectorEventLoop.__init__ = _init
            asyncio.BaseEventLoop.call_soon_threadsafe = _call_soon_threadsafe
            asyncio.BaseEventLoop.close = _close
        _ACTIVE.append(self)
        # Loops that already exist (the jobs background loop, a running test loop).
        try:
            asyncio_compat = self.asyncio_bridge
            bg = getattr(asyncio_compat, "_LOOP", None)
            if bg is not None and not bg.is_closed():
                self.wrap_loop(bg)
        except ImportError:
            pass
        try:
            running = asyncio.get_running_loop()
            self.wrap_loop(running)
        except RuntimeError:
            pass
        self.installed = True
        return self

    def uninstall(self) -> None:
        if not self.installed:
            return
        self.installed = False
        for loop, real in self._wrapped_loops:
            if isinstance(getattr(loop, "_selector", None), _VirtualSelector):
                loop._selector = real
            if getattr(loop, "_sim_real_resolution", None) is not None:
                loop._clock_resolution = loop._sim_real_resolution
        self._wrapped_loops.clear()
        if self in _ACTIVE:
            _ACTIVE.remove(self)
        if not _ACTIVE:
            asyncio.BaseEventLoop.time = _real_loop_time
            asyncio.BaseEventLoop.call_later = _real_call_later
            asyncio.BaseEventLoop.call_at = _real_call_at
            asyncio.selector_events.BaseSelectorEventLoop.__init__ = _real_selector_init
            asyncio.BaseEventLoop.call_soon_threadsafe = _real_call_soon_threadsafe
            asyncio.BaseEventLoop.close = _real_loop_close
        if self._traveller is not None:
            self._traveller.stop()
            self._traveller = None
            self._coordinates = None
        if self.seam is not None:
            self.seam.uninstall()

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False


# ---------------------------------------------------------------------------
# Execution fence
# ---------------------------------------------------------------------------

_FENCED_ITEMS: set = set()
_FENCED_THREADS: set = set()
_FROZEN_THREADS: set = set()
_GUARDED_CODES: set = set()      # our own blocking frames: they freeze on return themselves
_WATCHED: dict = {}              # code object -> fenced thread ids that may still continue in it
_FENCE_TOOL = 4
_fence_lock = threading.Lock()
_fence_installed = False
_freezing = threading.local()
_critical_depth = threading.local()   # C-level: reading it enters no Python function


class _Critical:
    """``with critical:`` marks a section a fenced thread must not freeze inside,
    because it holds a lock the rest of the process needs (the scheduler's
    condition, the ledger's lock, ``concurrent.futures``' submit locks). While
    it runs harness code there (``_harness_code``) the thread continues, and
    it freezes as it leaves the outermost section; the first application
    function it enters there freezes it at once."""
    __slots__ = ()

    def __enter__(self):
        _critical_depth.n = getattr(_critical_depth, "n", 0) + 1

    def __exit__(self, *exc):
        _critical_depth.n -= 1
        if not _critical_depth.n:
            _freeze_if_fenced()
        return False


critical = _Critical()


class _FenceSafeCondition(threading.Condition):
    """The scheduler's condition: holding it is a critical section. The depth is
    raised before the lock is taken and lowered after it is released, so no
    instruction between the two can freeze the holder."""

    def __enter__(self):
        critical.__enter__()
        return super().__enter__()

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            critical.__exit__()


def _stdlib_root() -> str:
    return os.path.realpath(sysconfig.get_paths()["stdlib"]) + os.sep


def _package_root(module) -> str:
    return os.path.dirname(os.path.realpath(module.__file__)) + os.sep


# Code a fenced thread may keep running inside a critical section: the standard
# library (not its site-packages), this harness, the harness's own dependency
# ``time_machine`` (the patched clock calls back into it), and generated code
# (``<frozen ...>`` modules, dataclass / namedtuple methods). The harness calls no
# application function inside a section; an application function generated from
# a string would be trusted there as well.
_HARNESS_ROOTS = (_package_root(time_machine), os.path.dirname(os.path.realpath(__file__)) + os.sep)
_TRUSTED_FILES: dict = {}


def _harness_code(code) -> bool:
    filename = code.co_filename
    trusted = _TRUSTED_FILES.get(filename)
    if trusted is None:
        path = os.path.realpath(filename)
        trusted = _TRUSTED_FILES[filename] = filename.startswith("<") or path.startswith(_HARNESS_ROOTS) or (
            path.startswith(_stdlib_root()) and f"{os.sep}site-packages{os.sep}" not in path)
    return trusted


def _freeze_if_fenced(code=None) -> None:
    """Freeze now if this thread is fenced, unless it is inside a critical
    section running harness code (``code`` is the function being entered; None
    for the harness's own call as a section ends)."""
    if getattr(_freezing, "on", False):
        return
    if getattr(_critical_depth, "n", 0) and (code is None or _harness_code(code)):
        return
    tid = threading.get_ident()
    item = current_item.get(None)
    if tid in _FENCED_THREADS or (item is not None and id(item) in _FENCED_ITEMS):
        _freezing.on = True
        _thread_frozen(tid)
        _freeze_forever()


def guard_code(code) -> None:
    """Declare one of the simulator's own blocking frames. A fenced thread whose
    innermost Python frame is guarded needs no instruction watch: that frame
    checks abandonment when its C call returns and freezes before continuing."""
    _GUARDED_CODES.add(code)


def fence(item, thread_ids) -> None:
    """Contain ``item`` / ``thread_ids`` forever, whatever they are blocked in.

    Two layers, both via ``sys.monitoring``:

    * PY_START (global): the next Python function entered by a fenced thread,
      or by any thread running as the fenced item, freezes.
    * INSTRUCTION (local, on every code object currently on a fenced thread's
      stack): a C call (``time.sleep``, a lock, a socket) returning INTO an
      already-running Python frame never fires PY_START, and a builtin effect
      (``list.append``, a store) is not a Python call either; the very next
      bytecode instruction of that frame freezes instead. The watch is removed
      once the thread has frozen. A thread whose innermost frame is one of the
      simulator's own guarded frames gets no watch: that frame freezes on
      return by itself (so shared stdlib/asyncio code is never instrumented
      for a loop parked in the virtual selector or a worker waiting on its
      private loop).

    Inside a ``critical`` section (it holds a lock the engine or the
    interpreter needs) neither layer freezes the thread while it runs harness
    code (``_harness_code``): it finishes that bookkeeping, releases the lock,
    and freezes as it leaves the outermost section. Nothing after the section
    runs, nor any handler or ``finally`` of its callers. The first application
    function entered inside a section freezes at once, holding the section's
    lock (the harness calls none there); the engine's coordinator then blocks
    on that lock too, so only the CLI's per-scenario process wall time contains
    the hang. A lock the application itself holds is not known here: a thread
    frozen holding one blocks whoever needs it next, which the engine's
    real-time budget turns into an INCOMPLETE run (see the simplification
    doc, section 8). An effect a C function already started (a socket write, a
    ``time.sleep``) completes; the fence acts at the next Python instruction.
    """
    global _fence_installed
    import sys  # noqa: PLC0415
    with _fence_lock:
        _FENCED_ITEMS.add(id(item))
        tids = {int(t) for t in thread_ids if t}
        _FENCED_THREADS.update(tids)
        if not _fence_installed:
            _install_fence()
            _fence_installed = True
        mon = getattr(sys, "monitoring", None)
        if mon is None:
            return
        frames = sys._current_frames()
        for tid in tids:
            f = frames.get(tid)
            if f is None or f.f_code in _GUARDED_CODES or f.f_code is _freeze_forever.__code__:
                continue
            while f is not None:
                code = f.f_code
                _WATCHED.setdefault(code, set()).add(tid)
                try:
                    mon.set_local_events(_FENCE_TOOL, code, mon.events.INSTRUCTION)
                except Exception:  # noqa: BLE001 — a code object that cannot be watched
                    pass
                f = f.f_back


def _freeze_forever():
    # A raw lock acquired twice: blocks forever with THIS frame innermost (no
    # stdlib Python frames to instrument), on a daemon thread.
    lk = threading.Lock()
    lk.acquire()
    lk.acquire()


def _thread_frozen(tid) -> None:
    import sys  # noqa: PLC0415
    mon = sys.monitoring
    with _fence_lock:
        _FROZEN_THREADS.add(tid)
        for code, tids in list(_WATCHED.items()):
            tids.discard(tid)
            if not tids:
                del _WATCHED[code]
                try:
                    mon.set_local_events(_FENCE_TOOL, code, 0)
                except Exception:  # noqa: BLE001
                    pass


def _install_fence() -> None:
    import sys  # noqa: PLC0415
    mon = getattr(sys, "monitoring", None)
    if mon is None:  # pragma: no cover - Python < 3.12
        return
    try:
        mon.use_tool_id(_FENCE_TOOL, "sim-fence")
    except ValueError:
        pass

    def on_start(code, offset):
        _freeze_if_fenced(code)
        return None

    def on_instruction(code, offset):
        tid = threading.get_ident()
        if tid not in _FENCED_THREADS or getattr(_freezing, "on", False):
            return None
        if getattr(_critical_depth, "n", 0) and _harness_code(code):
            return None
        _freezing.on = True
        _thread_frozen(tid)
        _freeze_forever()
        return None

    mon.register_callback(_FENCE_TOOL, mon.events.PY_START, on_start)
    mon.register_callback(_FENCE_TOOL, mon.events.INSTRUCTION, on_instruction)
    mon.set_events(_FENCE_TOOL, mon.events.PY_START)


def fence_active() -> bool:
    return _fence_installed


guard_code(_VirtualSelector.select.__code__)
