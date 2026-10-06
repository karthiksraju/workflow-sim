"""Domain-independent discrete-event engine.

The engine knows nothing about meetings. It owns:

* the virtual clock and the park registry (``clock.py``): every event loop in
  the process runs on virtual time and parks at its selector when idle;
* a heap of timeline **items** (scenario mutations, periodic beats) with
  explicit timestamps and insertion-order tie-breaking;
* the virtual Celery queue (``celery_driver.py``), whose due tasks are
  interleaved with items by time, then priority, then sequence;
* **executions**: every item callback and every task body runs in its own
  worker thread with its own private event loop thread (the patched
  ``run_coro_sync`` submits there). Only one execution *runs* at a time; an
  execution whose loops are all parked hands control back, so other work due
  before its next timer runs first. Time jumps only when everything is parked,
  and never past the run horizon. Parked loops are woken one at a time in
  (deadline, park order): equal deadlines resume deterministically;
* a hard-crash model: an abandoned execution is never resumed and is fenced
  (``clock.fence``) so no later wake-up, cleanup or boundary call can run;
  graceful cancellation is a separate path that does run ``finally`` blocks;
* ownership of thread-pool work: an execution is in flight until its body
  returned and every pool call it submitted (``asyncio.to_thread``,
  ``loop.run_in_executor``) has closed, and abandonment covers those calls too
  (``Engine._patch_run_in_executor``);
* seeded randomness (``random`` per execution, deterministic ``uuid4``);
* hard budgets (steps, real seconds) with an explicit stop reason;
* a ledger that records every execution with its causal parent.

Workflow adapters supply application boundaries and assertions.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import concurrent.futures.thread as _cf_thread
import contextlib
import contextvars
import heapq
import random
import threading
import time as _real_time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from workflow_sim import runtime as asyncio_compat
from workflow_sim.celery_driver import TaskRun, VirtualCelery
from workflow_sim.clock import (SimulatedWorkerCrash, VirtualClock, _freeze_forever, critical, current_item, fence,
                                   guard_code)
from workflow_sim.ledger import Ledger

try:  # the ledger's causal-parent contextvar (added by the evidence unit)
    from workflow_sim.ledger import current_cause
except ImportError:  # pragma: no cover - transitional
    current_cause = contextvars.ContextVar("sim_current_cause", default=None)


class UnsupportedFeature(RuntimeError):
    """The scenario asked for behaviour the engine deliberately does not model."""


_owned_pool_submit: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sim_owned_pool_submit", default=False)
_managed_thread_start: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sim_managed_thread_start", default=False)
_pool_owner: contextvars.ContextVar[Optional["WorkItem"]] = contextvars.ContextVar(
    "sim_pool_owner", default=None)


@dataclass(order=True)
class Item:
    at: datetime
    seq: int
    kind: str = field(compare=False)
    label: str = field(compare=False)
    fn: Callable[[], Any] = field(compare=False, repr=False)
    priority: int = field(compare=False, default=0)


def _noop_cb():
    pass


class WorkItem:
    """One execution (an item callback or a task body) running in a worker thread."""

    def __init__(self, kind: str, label: str, run: Optional[TaskRun] = None):
        self.kind = kind
        self.label = label
        self.run = run
        self.sync_blocked = 0        # worker thread waiting in run_coro_sync
        self.started_at: Optional[datetime] = None
        self.finished = False
        self.future: Optional[concurrent.futures.Future] = None
        self.error: Optional[BaseException] = None
        self.crashed = False
        self.abandoned = False
        self.thread: Optional[threading.Thread] = None
        self.thread_id: Optional[int] = None
        self.loop = None
        self.loop_thread: Optional[threading.Thread] = None
        self.loop_thread_id: Optional[int] = None
        self.crash_at_first_park: Optional[str] = None
        self.soft_limit_exc: Optional[BaseException] = None
        self.limit_items: list = []
        # Thread-pool calls (``asyncio.to_thread`` / ``run_in_executor``) not yet
        # closed, see ``Engine._patch_run_in_executor``.
        self.executor_calls: list[dict] = []
        self.loop_retired = False      # its private loop decided to exit (``_VirtualSelector.select``)

    @property
    def id(self) -> str:
        return self.run.task_id if self.run is not None else f"{self.kind}:{self.label}"

    def loop_ids(self) -> set:
        # Thread IDs can be reused once the entry body returns. A descendant can
        # outlive that body; fencing its former ID would freeze an unrelated later
        # execution (including the callback requesting this crash).
        return {thread.ident for thread in (self.thread, self.loop_thread)
                if thread is not None and thread.is_alive() and thread.ident is not None}

    def retired(self) -> bool:
        """The body, owned pool work and private loop descendants have finished."""
        return self.abandoned or (self.finished and not self.executor_calls
                                  and (self.loop is None or self.loop_retired))

    def settled(self) -> bool:
        return self.retired()

    def is_parked(self, scheduler) -> bool:
        """Parked = nothing of this execution can run until the engine acts.

        The worker thread must be waiting (inside a parked loop it drives with
        ``asyncio.run`` or inside ``run_coro_sync``), and every loop owned by
        the execution must be inside ``select`` with no wake pending. An execution
        whose body returned is parked only while each of its open pool calls runs
        on a thread that itself waits on the engine.
        """
        if self.settled():
            return False
        parks = scheduler.parks_for(self)
        if any(p.woken or scheduler.pending_for(p.loop) for p in parks):
            return False
        if self.loop is not None and scheduler.pending_for(self.loop):
            return False
        parked_threads = {p.thread_id for p in parks}
        worker_waiting = self.finished or self.sync_blocked > 0 or self.thread_id in parked_threads
        if not worker_waiting:
            return False
        if self.loop_thread_id is not None and self.loop_thread_id not in parked_threads:
            return False
        # Thread-pool work runs in real time: the execution is runnable until the
        # result is back on its loop, unless the pool thread itself waits on the engine.
        for call in self.executor_calls:
            if not call["running"] or not scheduler.thread_waiting(call["thread_id"]):
                return False
        return True

    # -- private loop --------------------------------------------------------------
    def ensure_loop(self):
        if self.loop is not None:
            return self.loop
        ready = threading.Event()
        box: dict = {}
        work = self

        def run_loop():
            loop = asyncio.new_event_loop()
            loop._sim_item = work
            asyncio.set_event_loop(loop)
            box["loop"] = loop
            work.loop_thread_id = threading.get_ident()
            ready.set()
            loop.run_forever()
            # The loop retires once its execution finished and nothing is left
            # scheduled on it (see _VirtualSelector.select); its thread exits.
            if not loop.is_closed():
                loop.close()

        ctx = contextvars.copy_context()
        thread = threading.Thread(target=lambda: ctx.run(run_loop), name=f"sim-loop-{self.id[:8]}", daemon=True)
        token = _managed_thread_start.set(True)
        try:
            thread.start()
        finally:
            _managed_thread_start.reset(token)
        ready.wait()
        self.loop = box["loop"]
        self.loop_thread = thread
        return self.loop


@dataclass
class RunReport:
    stop_reason: str
    started_at: datetime
    ended_at: datetime
    steps: int
    in_flight: list[dict]
    pending_tasks: list[dict]
    pending_items: list[dict]
    task_failures: list[dict]
    callback_failures: list[dict]
    crashes: list[dict]
    budget: dict
    dropped_timers: list[dict] = field(default_factory=list)


class Engine:
    def __init__(self, *, start: datetime, seed: int = 0, max_steps: int = 200_000,
                 max_real_seconds: float = 600.0, concurrency: Optional[int] = None,
                 clock_seam=None, asyncio_bridge=None, log_module=None, strict_lifecycle=False):
        self.strict_lifecycle = strict_lifecycle
        self._async_futures = []
        self._async_failures = []
        self.start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        self.seed = int(seed)
        self.asyncio_bridge = asyncio_bridge or asyncio_compat
        self.clock = VirtualClock(self.start, clock_seam=clock_seam, asyncio_bridge=self.asyncio_bridge)
        self.ledger = Ledger(self.clock.now, log_module=log_module)
        self.celery = VirtualCelery(self.clock, seed=self.seed)
        self._heap: list[Item] = []
        self._seq = 0
        self.max_steps = max_steps
        self.max_real_seconds = max_real_seconds
        self.executions: list[WorkItem] = []
        self.active: list[WorkItem] = []
        self._budget_freeze: Optional[list] = None
        self.concurrency = concurrency
        self.abandoned: list[WorkItem] = []
        self.unsupported = self.celery.unsupported
        self._installed = False
        self._stages: list[Callable[[], None]] = []
        self._orig_run_coro_sync = None
        self._orig_run_in_executor = None
        self._orig_shutdown_default_executor = None
        self._orig_loop_close = None
        self._orig_thread_start = None
        self._orig_uuid4 = None
        self._kombu_uuid_defaults = None
        self._uuid_rng = random.Random(f"uuid:{self.seed}")
        self.reports: list[RunReport] = []
        self._steps = 0
        self.horizon: Optional[datetime] = None
        self._crash_traps: list[dict] = []

    # -- lifecycle -------------------------------------------------------------
    def install(self) -> "Engine":
        """Install every global hook. Each stage is rolled back independently if a
        later stage fails, so a partial installation never leaks a patch."""
        if self._installed:
            return self
        self._stages = []
        try:
            self.clock.install()
            self._stages.append(self.clock.uninstall)
            self.clock.scheduler.engine = self
            self._stages.append(lambda: setattr(self.clock.scheduler, "engine", None))
            self.ledger.install()
            self._stages.append(self.ledger.uninstall)
            self.celery.install()
            self._stages.append(self.celery.uninstall)
            self._patch_run_coro_sync()
            self._stages.append(self._unpatch_run_coro_sync)
            self._patch_async_tracking()
            self._stages.append(self._unpatch_async_tracking)
            self._patch_uuid()
            self._stages.append(self._unpatch_uuid)
            self._patch_run_in_executor()
            self._stages.append(self._unpatch_run_in_executor)
            self._patch_pool_submit()
            self._stages.append(self._unpatch_pool_submit)
            self._patch_thread_start()
            self._stages.append(self._unpatch_thread_start)
            self._ambient_random_state = random.getstate()
            random.seed(f"engine:{self.seed}")
            self._stages.append(lambda: random.setstate(self._ambient_random_state))
        except BaseException:
            self._rollback_stages()
            raise
        self._installed = True
        return self

    def _rollback_stages(self) -> None:
        while self._stages:
            undo = self._stages.pop()
            try:
                undo()
            except Exception:  # noqa: BLE001 — keep unwinding
                pass

    def uninstall(self) -> None:
        """Abandon in-flight work FIRST (while every hook is still installed and
        the abandoned executions are fenced), then unwind the install stages."""
        if not self._installed:
            self._rollback_stages()
            return
        try:
            self._abort_in_flight("uninstall")
        finally:
            self._rollback_stages()
            self._installed = False
            self._stop_loops()

    def on_timers_dropped(self, item, handles) -> None:
        """A loop of ``item`` closed with timers still scheduled: those callbacks
        will never run. Recorded as a fault so the run cannot pass silently; the
        production counterpart (``asyncio.run`` returning) drops them the same way."""
        self.ledger.add("fault", "timer_dropped_at_loop_close",
                        data={"execution": getattr(item, "id", None), "label": getattr(item, "label", None),
                              "callbacks": [getattr(getattr(h, "_callback", None), "__qualname__", "?")
                                            for h in handles],
                              "due_in_seconds": [round(h.when() - h._loop.time(), 6) for h in handles]},
                        level="warning")

    def _stop_loops(self) -> None:
        for w in self.executions:
            if w.loop is not None and not w.abandoned:
                try:
                    w.loop.call_soon_threadsafe(w.loop.stop)
                except RuntimeError:
                    pass

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False

    # -- patches -----------------------------------------------------------------
    def _patch_run_coro_sync(self) -> None:
        engine = self
        self._orig_run_coro_sync = self.asyncio_bridge.run_coro_sync

        def sim_run_coro_sync(coro):
            """Run ``coro`` on the calling execution's private loop (or, outside an
            execution, on the shared background loop). The worker thread is
            accounted as blocked while it waits; an abandoned execution never
            gets its result back."""
            item = current_item.get(None) or _pool_owner.get(None)
            if item is None and engine.strict_lifecycle:
                reason = "run_coro_sync outside an owned execution; schedule adapter work with ctx.at"
                engine.unsupported.append(reason)
                coro.close()
                raise UnsupportedFeature(reason)
            if item is not None:
                # Raw run_in_executor workers do not copy current_item. Their
                # async descendants still belong to the pool call's execution.
                loop = item.ensure_loop()
            else:
                # Coordinator use of the experimental in-process kernel only.
                token = _managed_thread_start.set(True)
                try:
                    loop = self.asyncio_bridge._ensure_background_loop()
                finally:
                    _managed_thread_start.reset(token)
            ctx = contextvars.copy_context()
            gate = threading.Lock()      # raw lock: this frame stays innermost while blocked
            gate.acquire()
            box: dict = {}

            sched = engine.clock.scheduler

            tid = threading.get_ident()
            # Only the execution's worker thread counts as its blocked worker; a
            # pool thread running as the execution (``asyncio.to_thread`` copies
            # the context) is accounted through its pool call instead.
            worker = item is not None and tid == item.thread_id

            def _release():
                # The worker stops counting as blocked *before* it is signalled, so
                # the engine never observes "loop idle + worker blocked" in the
                # window between ``done.set()`` and the worker thread waking up.
                with sched.cv:
                    if not box.get("released"):
                        box["released"] = True
                        sched.block_thread(tid, -1)
                        if worker:
                            item.sync_blocked -= 1
                    sched.cv.notify_all()

            async def _wrapped():
                try:
                    box["value"] = await coro
                except asyncio.CancelledError as exc:
                    box["error"] = item.soft_limit_exc if (item is not None and item.soft_limit_exc) else exc
                except BaseException as exc:  # noqa: BLE001 — propagate to the caller thread
                    box["error"] = exc
                finally:
                    _release()
                    gate.release()

            try:
                with sched.cv:
                    sched.block_thread(tid, 1, loop)
                    if worker:
                        item.sync_blocked += 1
                    armed = worker and item.crash_at_first_park
                    if not armed:
                        # Posting and marking the caller blocked are one scheduler
                        # transition. Otherwise it can observe an idle loop in the
                        # gap and advance to the horizon before this coroutine runs.
                        loop.call_soon_threadsafe(lambda: loop.create_task(_wrapped(), context=ctx))
                    sched.cv.notify_all()
                if armed:
                    # An armed first-park crash must never start the coroutine.
                    coro.close()
                    _freeze_forever()
                gate.acquire()
                if item is not None and item.abandoned:
                    _freeze_forever()    # guarded frame: an abandoned worker never continues
            finally:
                _release()
            if "error" in box:
                raise box["error"]
            return box.get("value")

        guard_code(sim_run_coro_sync.__code__)
        self._sim_run_coro_sync = sim_run_coro_sync
        self.asyncio_bridge.run_coro_sync = sim_run_coro_sync
        for mod in list(_run_coro_sync_importers()):
            if getattr(mod, "run_coro_sync", None) is self._orig_run_coro_sync:
                setattr(mod, "run_coro_sync", sim_run_coro_sync)

    def _patch_async_tracking(self):
        # Retain tasks/futures until the evidence snapshot: collection timing must
        # not determine whether an unobserved error or pending task is detectable.
        engine = self
        self._orig_create_task = asyncio.BaseEventLoop.create_task
        self._orig_create_future = asyncio.BaseEventLoop.create_future
        self._orig_exception_handler = asyncio.BaseEventLoop.call_exception_handler

        def track(loop, future):
            owner = current_item.get(None) or getattr(loop, "_sim_item", None)
            engine._async_futures.append((future, owner))
            return future

        def create_task(loop, coro, *args, **kwargs):
            return track(loop, engine._orig_create_task(loop, coro, *args, **kwargs))

        def create_future(loop):
            return track(loop, engine._orig_create_future(loop))

        def exception_handler(loop, context):
            future = context.get("future") or context.get("task")
            owner = next((w for f, w in engine._async_futures if f is future), None)
            owner = owner or current_item.get(None) or getattr(loop, "_sim_item", None)
            if owner is None or not owner.abandoned:
                exc = context.get("exception")
                error = f"{type(exc).__name__}: {exc}" if exc is not None else context.get("message", "asyncio error")
                engine._async_failures.append({"kind": "asyncio", "label": owner.label if owner else "unowned",
                                               "error": error})
            # Preserve the application's handler, including its normal context.
            engine._orig_exception_handler(loop, context)

        asyncio.BaseEventLoop.create_task = create_task
        asyncio.BaseEventLoop.create_future = create_future
        asyncio.BaseEventLoop.call_exception_handler = exception_handler

    def _observe_async_errors(self):
        for future, owner in list(self._async_futures):
            if owner is not None and owner.abandoned:
                continue
            # CPython 3.12 Future records whether result()/exception()/await has
            # consumed the exception. Looking at done() alone misclassifies caught
            # exceptions; waiting for __del__ misses deliberately retained tasks.
            if future.done() and getattr(future, "_log_traceback", False):
                exc = future._exception
                future._log_traceback = False
                future.get_loop().call_exception_handler({
                    "message": f"{type(future).__name__} exception was never retrieved",
                    "exception": exc, "future": future})

    def _unpatch_async_tracking(self):
        asyncio.BaseEventLoop.create_task = self._orig_create_task
        asyncio.BaseEventLoop.create_future = self._orig_create_future
        asyncio.BaseEventLoop.call_exception_handler = self._orig_exception_handler

    def _patch_run_in_executor(self) -> None:
        """An execution owns the thread-pool calls it makes (``loop.run_in_executor``,
        and ``asyncio.to_thread`` through it).

        A call is open from submission until BOTH its pool half closed (the function
        returned, or the submission was cancelled before it started) AND its loop
        half closed (the loop-side future is done: the result arrives through
        ``call_soon_threadsafe`` after the function returns, and a cancelled await
        leaves the function running). The execution stays in flight while any call
        is open (``WorkItem.retired``). Abandoning it (``crash_execution``): a
        queued submission never starts (``run`` checks abandonment when the pool
        dequeues it); running calls are fenced with the execution and their
        threads are detached from every exit join (``_detach_from_exit_joins``).
        Closing an application loop closes only that call's loop half; a custom
        pool's still-running function remains owned until it returns.
        ``ThreadPoolExecutor.submit`` is a critical section for the fence while
        the engine is installed (``_patch_pool_submit``)."""
        engine = self
        orig = self._orig_run_in_executor = asyncio.BaseEventLoop.run_in_executor
        sched = self.clock.scheduler

        def sim_run_in_executor(loop_self, executor, func, *args):
            item = current_item.get(None) or getattr(loop_self, "_sim_item", None)
            if item is None or sched.engine is not engine:
                return orig(loop_self, executor, func, *args)
            if not _EXIT_JOINS_KNOWN:
                raise UnsupportedFeature("thread-pool work under the engine: this interpreter lacks the exit-join "
                                         "registries an abandoned pool thread must be detached from")
            loop_self._check_closed()
            if executor is None:
                # The stdlib's default-executor resolution, so the call knows its pool.
                executor = loop_self._default_executor
                loop_self._check_default_executor()
                if executor is None:
                    executor = concurrent.futures.ThreadPoolExecutor(thread_name_prefix="asyncio")
                    loop_self._default_executor = executor
            call = {"kind": "call", "running": False, "started": False, "pool_done": False, "thread_id": None,
                    "thread": None, "executor": executor, "future": None, "loop": loop_self,
                    "pool_closed": False, "loop_closed": False, "open": 2}

            def close_half(half):
                # No wake-up of the private loop is needed when the pool half closes
                # last: asyncio delivers every pool result to an open loop through
                # ``call_soon_threadsafe`` after the function returns. If the loop
                # has already closed, its half is closed explicitly below.
                with sched.cv:
                    key = half + "_closed"
                    if call[key]:
                        return
                    call[key] = True
                    call["open"] -= 1
                    if call["open"] == 0 and call in item.executor_calls:
                        item.executor_calls.remove(call)
                    sched.cv.notify_all()

            def close_pool(_=None):
                close_half("pool")

            def close_loop(_=None):
                close_half("loop")

            call["close_loop"] = close_loop

            def run(*a):
                thread = threading.current_thread()
                with sched.cv:
                    start = not item.abandoned
                    if start:
                        call.update(started=True, running=True, thread_id=threading.get_ident(), thread=thread)
                if not start:
                    close_pool()   # dequeued after its execution was abandoned: never starts
                    return None
                try:
                    token = _pool_owner.set(item)
                    try:
                        return func(*a)
                    finally:
                        _pool_owner.reset(token)
                finally:
                    with sched.cv:
                        call["running"], call["pool_done"] = False, True
                    close_pool()

            def closed_if_cancelled(f):
                if f.cancelled():   # cancelled while queued: ``run`` never runs
                    close_pool()

            with sched.cv:
                item.executor_calls.append(call)
            try:
                fut = orig(loop_self, _Submission(executor, call), run, *args)
            except BaseException:
                with sched.cv:
                    item.executor_calls.remove(call)
                raise
            call["future"].add_done_callback(closed_if_cancelled)
            fut.add_done_callback(close_loop)
            return fut

        orig_shutdown = self._orig_shutdown_default_executor = asyncio.BaseEventLoop.shutdown_default_executor

        async def sim_shutdown_default_executor(loop_self, *args, **kwargs):
            # ``asyncio.run`` joins the default pool on a raw thread (under a timeout
            # in *virtual* time): the loop is runnable until the join is back.
            item = current_item.get(None) or getattr(loop_self, "_sim_item", None)
            if item is None or sched.engine is not engine or loop_self._default_executor is None:
                return await orig_shutdown(loop_self, *args, **kwargs)
            call = {"kind": "join", "running": False, "thread_id": None, "open": 1}
            with sched.cv:
                item.executor_calls.append(call)
            try:
                token = _managed_thread_start.set(True)
                try:
                    return await orig_shutdown(loop_self, *args, **kwargs)
                finally:
                    _managed_thread_start.reset(token)
            finally:
                with sched.cv:
                    item.executor_calls.remove(call)
                    sched.cv.notify_all()

        asyncio.BaseEventLoop.run_in_executor = sim_run_in_executor
        asyncio.BaseEventLoop.shutdown_default_executor = sim_shutdown_default_executor
        orig_close = self._orig_loop_close = asyncio.BaseEventLoop.close

        def sim_loop_close(loop_self):
            orig_close(loop_self)
            # asyncio.run joins only its default pool. An un-awaited call on an
            # application-owned pool can outlive loop.close; asyncio's chained
            # Future will never become done because the loop discarded its ready
            # callbacks. Close that half now, but keep the execution in flight
            # until the pool function itself returns (or is abandoned/fenced).
            if sched.engine is engine:
                with sched.cv:
                    pending = [c["close_loop"] for item in engine.active for c in item.executor_calls
                               if c.get("kind") == "call" and c.get("loop") is loop_self]
                for close in pending:
                    close()

        asyncio.BaseEventLoop.close = sim_loop_close

    def _patch_pool_submit(self) -> None:
        """``ThreadPoolExecutor.submit`` holds its pool's and concurrent.futures'
        global shutdown locks while it starts a worker thread: a critical section,
        so a crash landing inside it never freezes a thread holding them (every
        later submit and the interpreter's exit hook need them). A subclass's own
        ``submit`` is application code and stays outside the section."""
        orig_submit = self._orig_pool_submit = concurrent.futures.ThreadPoolExecutor.submit

        def sim_submit(pool_self, fn, /, *args, **kwargs):
            item = current_item.get(None) or _pool_owner.get(None)
            if (item is not None or self.strict_lifecycle) and self.clock.scheduler.engine is self and not _owned_pool_submit.get():
                reason = (f"direct ThreadPoolExecutor.submit in {item.label if item else 'adapter lifecycle'}: the submitted function "
                          "has no execution ownership; use loop.run_in_executor or asyncio.to_thread")
                self.unsupported.append(reason)
                self.ledger.add("fault", "unsupported_pool_submit", data={"execution": item.id if item else None,
                                                                            "label": item.label if item else "adapter lifecycle"})
                raise UnsupportedFeature(reason)
            with critical:
                return orig_submit(pool_self, fn, *args, **kwargs)

        concurrent.futures.ThreadPoolExecutor.submit = sim_submit

    def _patch_thread_start(self) -> None:
        """Reject an application thread that would escape execution ownership.

        The engine's private loop, an owned pool submission, and asyncio's
        default-executor join are marked at their actual start sites. A raw
        application ``Thread.start`` has no completion/fence contract.
        """
        orig = self._orig_thread_start = threading.Thread.start

        def sim_start(thread_self, *args, **kwargs):
            item = current_item.get(None) or _pool_owner.get(None)
            if ((item is not None or self.strict_lifecycle) and self.clock.scheduler.engine is self
                    and not _managed_thread_start.get() and not _owned_pool_submit.get()):
                reason = (f"direct Thread.start in {item.label if item else 'adapter lifecycle'}: the new thread has no execution "
                          "ownership; use loop.run_in_executor or asyncio.to_thread")
                self.unsupported.append(reason)
                self.ledger.add("fault", "unsupported_thread_start", data={"execution": item.id if item else None,
                                                                            "label": item.label if item else "adapter lifecycle"})
                raise UnsupportedFeature(reason)
            return orig(thread_self, *args, **kwargs)

        threading.Thread.start = sim_start

    def _unpatch_thread_start(self) -> None:
        threading.Thread.start = self._orig_thread_start

    def _unpatch_pool_submit(self) -> None:
        concurrent.futures.ThreadPoolExecutor.submit = self._orig_pool_submit

    def _unpatch_run_in_executor(self) -> None:
        asyncio.BaseEventLoop.run_in_executor = self._orig_run_in_executor
        asyncio.BaseEventLoop.shutdown_default_executor = self._orig_shutdown_default_executor
        asyncio.BaseEventLoop.close = self._orig_loop_close

    def _unpatch_run_coro_sync(self) -> None:
        self.asyncio_bridge.run_coro_sync = self._orig_run_coro_sync
        for mod in list(_run_coro_sync_importers()):
            if getattr(mod, "run_coro_sync", None) is self._sim_run_coro_sync:
                setattr(mod, "run_coro_sync", self._orig_run_coro_sync)

    def _patch_uuid(self) -> None:
        self._orig_uuid4 = uuid.uuid4
        rng = self._uuid_rng

        def uuid4():
            return uuid.UUID(int=rng.getrandbits(128), version=4)

        self._sim_uuid4 = uuid4
        uuid.uuid4 = uuid4
        self.rebind_uuid()
        # kombu's ``uuid()`` (re-exported as ``celery.utils.uuid``; it names every
        # canvas signature a chain freezes) captured the original ``uuid4`` as a
        # default argument, which no module rebinding reaches.
        try:
            from kombu.utils.uuid import uuid as kombu_uuid  # noqa: PLC0415 — lazy: optional dependency
        except ImportError:
            return
        self._kombu_uuid = kombu_uuid
        self._kombu_uuid_defaults = self._kombu_uuid.__defaults__
        self._kombu_uuid.__defaults__ = (uuid4,)

    def rebind_uuid(self) -> int:
        """Modules that did ``from uuid import uuid4`` hold the original function;
        point them at the seeded one (call again after lazily importing modules)."""
        import sys  # noqa: PLC0415
        n = 0
        for mod in list(sys.modules.values()):
            if getattr(mod, "uuid4", None) is self._orig_uuid4:
                try:
                    setattr(mod, "uuid4", self._sim_uuid4)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        return n

    def _unpatch_uuid(self) -> None:
        import sys  # noqa: PLC0415
        for mod in list(sys.modules.values()):
            if getattr(mod, "uuid4", None) is getattr(self, "_sim_uuid4", None):
                setattr(mod, "uuid4", self._orig_uuid4)
        uuid.uuid4 = self._orig_uuid4
        if self._kombu_uuid_defaults is not None:
            self._kombu_uuid.__defaults__ = self._kombu_uuid_defaults

    # -- scheduling items ---------------------------------------------------------
    def at(self, when: datetime, kind: str, label: str, fn: Callable[[], Any], *,
           priority: int = 0) -> Item:
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        self._seq += 1
        item = Item(when, self._seq, kind, label, fn, priority)
        heapq.heappush(self._heap, item)
        return item

    def cancel_items(self, predicate: Callable[[Item], bool]) -> int:
        keep = [i for i in self._heap if not predicate(i)]
        removed = len(self._heap) - len(keep)
        self._heap = keep
        heapq.heapify(self._heap)
        return removed

    def pending_items(self) -> list[Item]:
        return sorted(self._heap)

    # -- the master loop -------------------------------------------------------------
    def _task_slots_free(self) -> bool:
        if self.concurrency is None:
            return True
        with self.clock.scheduler.cv:
            busy = sum(1 for w in self.active if w.kind == "task" and not w.finished)
        return busy < self.concurrency

    def _next_due(self) -> Optional[tuple[datetime, int, str]]:
        """Earliest of (item, task, parked loop timer). Tuple orders by time then a
        source rank so that at equal timestamps items run before tasks before
        timer wake-ups (explicit, documented tie rule). Parked loops wake ONE at
        a time in (deadline, park order); a task is only eligible when a worker
        slot is free (backpressure)."""
        cands = []
        if self._heap:
            cands.append((self._heap[0].at, 0, "item"))
        run = self.celery.peek()
        if run is not None and self._task_slots_free():
            cands.append((run.due_at, 1, "task"))
        park = self.clock.scheduler.earliest()
        if park is not None:
            cands.append((park.when, 2, "timer"))
        return min(cands) if cands else None

    def _freeze_unfinished(self) -> list:
        """Snapshot unfinished executions at budget detection. Call holding
        sched.cv (the _wait_until_parked site already does; step-budget sites
        take it around the call). The snapshot is carried on the exception and
        retained on the engine so evidence refreshes preserve it."""
        frozen = [w for w in self.active if not w.retired()]
        self._budget_freeze = list(frozen)
        return frozen

    def _wait_until_parked(self, real_budget_deadline: float) -> None:
        """Block until every active execution is parked or finished."""
        sched = self.clock.scheduler
        with sched.cv:
            while True:
                self.active = [w for w in self.active if not w.settled()]
                # A woken park is a loop that is running and has not re-parked
                # yet (this includes a finished execution's loop running a
                # late timer); the engine never advances past it.
                if all(w.is_parked(sched) for w in self.active) and not any(p.woken for p in sched.parks):
                    break
                remaining = real_budget_deadline - _real_time.monotonic()
                if remaining <= 0:
                    # Freeze atomically with detection: still holding sched.cv,
                    # snapshot before constructing (a stall between
                    # construction and handler must not move membership taken
                    # at detection).
                    frozen = self._freeze_unfinished()
                    exc = _BudgetExceeded("real time budget exhausted while work was running")
                    exc.interrupted = frozen
                    raise exc
                sched.cv.wait(timeout=min(0.05, remaining))
        # Armed crashes fire at the execution's first scheduling point.
        for w in list(self.active):
            if w.crash_at_first_park and not w.finished and w.is_parked(sched):
                reason, w.crash_at_first_park = w.crash_at_first_park, None
                self.crash_execution(w, reason=reason, hard=True)

    def _start_execution(self, kind: str, label: str, fn: Callable[[], Any],
                         run: Optional[TaskRun] = None, *, trap: Optional[dict] = None) -> WorkItem:
        work = WorkItem(kind, label, run)
        work.started_at = self.clock.now()
        if trap is not None:
            work.crash_at_first_park = trap["reason"]
        sched = self.clock.scheduler

        def job():
            token = current_item.set(work)
            cause = current_cause.set(work.id)
            work.thread_id = threading.get_ident()
            random.seed(f"{self.seed}:{work.id}:{getattr(run, 'retries', 0)}")
            try:
                return fn()
            except BaseException as exc:  # noqa: BLE001 — recorded, never propagates into the loop
                work.error = exc
                raise
            finally:
                current_cause.reset(cause)
                current_item.reset(token)

        future: concurrent.futures.Future = concurrent.futures.Future()
        work.future = future

        def _finished():
            # Only after the future's done callbacks ran (task finish + ledger
            # entry, on this thread): the engine must never observe "finished"
            # while the completion is still being recorded, or a later item
            # could log ahead of it (ledger order would depend on a race).
            with sched.cv:
                if not work.abandoned:
                    work.finished = True
                sched.cv.notify_all()
            if work.loop is not None and not work.abandoned:
                # Wake the private loop so it re-evaluates: with nothing
                # scheduled it retires; with a live timer it keeps parking
                # until that timer has run (the production background loop
                # would run it too).
                try:
                    work.loop.call_soon_threadsafe(_noop_cb)
                except RuntimeError:
                    pass

        def runner():
            try:
                try:
                    result = job()
                except BaseException as exc:  # noqa: BLE001 — recorded on the future
                    if not work.abandoned:
                        future.set_exception(exc)
                    return
                if not work.abandoned:
                    future.set_result(result)
            finally:
                _finished()

        thread = threading.Thread(target=runner, name=f"sim-worker-{work.id[:8]}", daemon=True)
        work.thread = thread
        self.executions.append(work)
        self.active.append(work)
        token = _managed_thread_start.set(True)
        try:
            thread.start()
        finally:
            _managed_thread_start.reset(token)
        return work

    def _run_item(self, item: Item) -> WorkItem:
        self.ledger.add(item.kind, item.label, data={"seq": item.seq})
        work = self._start_execution(item.kind, item.label, item.fn)

        def _done(f):
            if work.error is not None:
                self.ledger.add("scenario", "callback_failed", level="error",
                                data={"kind": item.kind, "label": item.label,
                                      "error": f"{type(work.error).__name__}: {work.error}"})
            self.clock.scheduler.notify()

        work.future.add_done_callback(_done)
        return work

    def arm_crash(self, predicate: Callable[[TaskRun], bool], *, mode: str = "at_first_park",
                  redeliver: bool = True, reason: str = "worker_crash") -> dict:
        """Crash the NEXT task execution matching ``predicate``: before it runs any
        effect (``at_start``) or at its first scheduling point (``at_first_park``)."""
        if mode not in ("at_first_park", "at_start"):
            raise UnsupportedFeature(f"worker.crash mode {mode!r}")
        trap = {"predicate": predicate, "mode": mode, "redeliver": redeliver, "reason": reason}
        self._crash_traps.append(trap)
        return trap

    def _run_task(self, run: TaskRun) -> WorkItem:
        self.celery.begin(run)
        trap = next((t for t in self._crash_traps if t["predicate"](run)), None)
        if trap is not None:
            self._crash_traps.remove(trap)
            run.redeliver = run.redeliver or trap["redeliver"]
            if trap["mode"] == "at_start":
                run.state = "CRASHED"
                run.error = f"crashed before start: {trap['reason']}"
                self.ledger.add("fault", "worker_crash", data={"target": run.task_id, "label": run.name,
                                                               "reason": trap["reason"], "mode": "at_start"})
                work = WorkItem("task", f"celery:{run.name.rsplit('.', 1)[-1]}", run)
                work.started_at = self.clock.now()
                work.finished = work.crashed = True
                self.executions.append(work)
                self.celery.finish(run)
                self._log_task(run)
                return work
        work = self._start_execution("task", f"celery:{run.name.rsplit('.', 1)[-1]}",
                                     lambda: self.celery.execute(run), run,
                                     trap=trap if trap and trap["mode"] == "at_first_park" else None)

        def _done(_f):
            if work.abandoned:
                return
            self._cancel_limits(work)
            try:
                self.celery.finish(run)
            except BaseException as exc:
                run.state = "FAILURE"
                run.error = f"completion_error:{type(exc).__name__}: {exc}"
            finally:
                self._log_task(run)
                self.clock.scheduler.notify()

        work.future.add_done_callback(_done)
        from celery.exceptions import SoftTimeLimitExceeded  # noqa: PLC0415 — lazy
        # Serialize registration against completion: a fast task may already
        # have finished before its deadline items are installed.
        with self.clock.scheduler.cv:
            if not work.future.done() and not work.abandoned:
                if run.soft_time_limit:
                    when = work.started_at + timedelta(seconds=float(run.soft_time_limit))
                    work.limit_items.append(self.at(when, "limit", f"soft_time_limit:{run.task_id[:8]}",
                        lambda w=work: self.cancel_execution(w, SoftTimeLimitExceeded(), reason="soft_time_limit"),
                        priority=10))
                if run.time_limit:
                    when = work.started_at + timedelta(seconds=float(run.time_limit))
                    work.limit_items.append(self.at(when, "limit", f"time_limit:{run.task_id[:8]}",
                        lambda w=work: self.crash_execution(w, reason="time_limit", hard=True), priority=10))
        return work

    def _cancel_limits(self, work):
        with self.clock.scheduler.cv:
            handles = {id(item) for item in work.limit_items}
            self.cancel_items(lambda item: id(item) in handles)
            work.limit_items.clear()

    def _log_task(self, run: TaskRun) -> None:
        self.ledger.add("task", f"celery:{run.name.rsplit('.', 1)[-1]}",
                        data={"state": run.state, "kwargs": run.kwargs, "task_id": run.task_id,
                              "result": _brief(run.result), "reason": run.error,
                              "retries": run.retries, "delivery": run.delivery})

    def step(self, *, horizon: Optional[datetime] = None) -> Optional[str]:
        """Run the single earliest due piece of work. Returns what ran
        ("item" / "task" / "timer") or None when nothing is due before the horizon."""
        deadline = _real_time.monotonic() + self.max_real_seconds
        self._wait_until_parked(deadline)
        nxt = self._next_due()
        if nxt is None:
            return None
        when, _rank, source = nxt
        if horizon is not None and when > horizon:
            return None
        self._steps += 1
        if self._steps > self.max_steps:
            with self.clock.scheduler.cv:
                frozen = self._freeze_unfinished()
            exc = _BudgetExceeded(f"step budget {self.max_steps} exhausted")
            exc.interrupted = frozen
            raise exc
        self.clock.advance_to(when, reason=source)
        if source == "item":
            self._run_item(heapq.heappop(self._heap))
        elif source == "task":
            self._run_task(self.celery.pop())
        else:
            park = self.clock.scheduler.earliest()
            if park is not None:
                self.clock.scheduler.wake(park)
        # Let the started/woken work run until it parks or finishes so that the
        # next decision sees its effects (deterministic handoff).
        self._wait_until_parked(deadline)
        return source

    def run_until(self, when: datetime) -> RunReport:
        """Run every piece of work due at or before ``when``; the clock never
        moves past ``when``. Returns a report with the stop reason and whatever
        is still pending (in-flight executions parked beyond the horizon)."""
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        self.horizon = when
        started = self.clock.now()
        real_start = _real_time.monotonic()
        steps_before = self._steps
        stop = "horizon"
        interrupted = None
        # Driving the scheduler is what makes parks engine-owned (no autojump,
        # hard horizon); a caller that installed only the clock gets that too.
        sched = self.clock.scheduler
        attached = sched.engine is None
        if attached:
            sched.engine = self
        try:
            while True:
                if _real_time.monotonic() - real_start > self.max_real_seconds:
                    # Snapshot before constructing: a test hook (or a real
                    # stall) between construction and handler must not move
                    # membership taken at detection.
                    with sched.cv:
                        frozen = self._freeze_unfinished()
                    outer = _BudgetExceeded("real time budget exhausted")
                    outer.interrupted = frozen
                    raise outer
                ran = self.step(horizon=when)
                if ran is None:
                    break
        except _BudgetExceeded as exc:
            stop = f"budget: {exc}"
            # Membership frozen at detection (carried on the exception); fall
            # back to a locked read for budget sources that bypass the wait.
            # Retained on the engine in all cases so evidence refreshes, which
            # pass no override, preserve exactly this set.
            interrupted = getattr(exc, 'interrupted', None)
            if interrupted is None:
                with sched.cv:
                    interrupted = [w for w in self.active if not w.retired()]
            self._budget_freeze = list(interrupted)
        finally:
            if attached:
                sched.engine = None
        self.clock.advance_to(when, reason="run_until")
        report = self._report(stop, started, steps_before,
                              in_flight_override=interrupted)
        self.reports.append(report)
        return report

    def run_all(self, *, max_steps: Optional[int] = None) -> RunReport:
        """Run until nothing is pending (no items, no tasks, no timers)."""
        started = self.clock.now()
        steps_before = self._steps
        stop = "quiescent"
        interrupted = None
        limit = max_steps or self.max_steps
        try:
            while self.step() is not None:
                if self._steps - steps_before > limit:
                    with self.clock.scheduler.cv:
                        frozen = self._freeze_unfinished()
                    step_exc = _BudgetExceeded(f"step budget {limit} exhausted")
                    step_exc.interrupted = frozen
                    raise step_exc
        except _BudgetExceeded as exc:
            stop = f"budget: {exc}"
            # Same freeze as run_until; fall back to a locked read for budget
            # sources that bypass the wait. Retained in all cases for refreshes.
            interrupted = getattr(exc, 'interrupted', None)
            if interrupted is None:
                with self.clock.scheduler.cv:
                    interrupted = [w for w in self.active if not w.retired()]
            self._budget_freeze = list(interrupted)
        report = self._report(stop, started, steps_before,
                              in_flight_override=interrupted)
        self.reports.append(report)
        return report

    def _report(self, stop: str, started: datetime, steps_before: int,
                in_flight_override=None) -> RunReport:
        self._observe_async_errors()
        sched = self.clock.scheduler
        with sched.cv:
            # With an override, frozen-at-detection membership is authoritative
            # for what was unfinished at the stop instant. Without one, a
            # retained budget freeze applies (evidence refreshes pass no
            # override); otherwise live state. Live unretired work is unioned
            # in so executions created afterwards (e.g. by assertion reads
            # during the worker's evidence refresh) stay visible too.
            retained = (self._budget_freeze if stop.startswith("budget:") else None)
            base = in_flight_override if in_flight_override is not None else retained
            members = list(base) if base is not None else []
            seen = {id(w) for w in members}
            members.extend(w for w in self.active
                           if not w.retired() and id(w) not in seen)
            in_flight = [{"id": w.id, "kind": w.kind, "label": w.label,
                          "since": w.started_at.isoformat() if w.started_at else None,
                          "deadlines": [p.when.isoformat() if p.when else None for p in sched.parks_for(w)],
                          "blocked_on_external": any(p.when is None for p in sched.parks_for(w))}
                         for w in members]
        return RunReport(
            stop_reason=stop, started_at=started, ended_at=self.clock.now(),
            steps=self._steps - steps_before, in_flight=in_flight,
            pending_tasks=[{"name": r.name, "due_at": r.due_at.isoformat(), "task_id": r.task_id,
                            "kwargs": r.kwargs} for r in self.celery.pending()],
            pending_items=[{"kind": i.kind, "label": i.label, "at": i.at.isoformat()}
                           for i in self.pending_items()],
            task_failures=[{"name": r.name, "task_id": r.task_id, "state": r.state, "error": r.error}
                           for r in self.celery.executed if r.state not in ("SUCCESS", "RETRY", "REVOKED")],
            callback_failures=[{"kind": w.kind, "label": w.label,
                                "error": f"{type(w.error).__name__}: {w.error}"}
                               for w in self.executions if w.run is None and w.error is not None
                               and not w.abandoned] + list(self._async_failures),
            crashes=[{"id": w.id, "label": w.label, "abandoned": w.abandoned} for w in self.executions if w.crashed],
            dropped_timers=list(self.clock.scheduler.dropped_timers),
            budget={"max_steps": self.max_steps, "max_real_seconds": self.max_real_seconds,
                    "concurrency": self.concurrency, "abandoned_threads": len(self.abandoned)},
        )

    # -- faults -------------------------------------------------------------------
    def crash_execution(self, work: WorkItem, *, reason: str = "worker_crash", hard: bool = True) -> bool:
        """Kill the worker running ``work``.

        ``hard`` (default) is a process kill: the execution is abandoned at its
        current scheduling point, its loops are never woken again, and its
        threads are fenced so that any later resumption (a released lock, an
        external future) freezes before executing another Python function. No
        ``finally`` runs; held leases stay held until they expire. The kill
        covers the execution's open pool calls, including after its body
        returned: a queued submission never starts, and a running pool
        function is fenced with it.
        ``hard=False`` delegates to :meth:`cancel_execution`.
        """
        if work.retired():
            return False
        if not hard:
            return self.cancel_execution(work, SimulatedWorkerCrash(reason), reason=reason)
        sched = self.clock.scheduler
        with sched.cv:
            if work.retired():
                return False
            returned = work.finished     # the body had returned; only its pool calls were left
            work.abandoned = True
            work.crashed = True
            work.finished = True
            if not returned:
                work.error = SimulatedWorkerCrash(reason)
            calls = [c for c in work.executor_calls if c["kind"] == "call"]
            running = [c for c in calls if c["started"] and not c["pool_done"]]
            queued = [c for c in calls if not c["started"]]
            dropped = sched.forget(work)
            # Under the scheduler's condition: a running call cannot mark its pool
            # half done and hand its thread back to the pool in between, so only
            # this execution's threads are fenced.
            fence(work, work.loop_ids() | {c["thread_id"] for c in running})
        self._cancel_limits(work)
        for c in running:
            _detach_from_exit_joins(c["thread"], c["executor"])
        self.abandoned.append(work)
        run = work.run
        if run is not None and not returned:
            run.state = "CRASHED"
            run.error = f"crashed: {reason}"
            self.celery.finish(run)
            self._log_task(run)
        if not returned:
            self.ledger.add("fault", "worker_crash", data={"target": work.id, "label": work.label,
                                                           "reason": reason, "mode": "hard",
                                                           "abandoned_parks": dropped})
        if calls:
            self.ledger.add("fault", "pool_work_abandoned",
                            data={"target": work.id, "label": work.label, "reason": reason,
                                  "after_return": returned, "running": len(running), "pending": len(queued)})
        sched.notify()
        return True

    def cancel_execution(self, work: WorkItem, exc: Optional[BaseException] = None, *,
                         reason: str = "cancel") -> bool:
        """Graceful cancellation: every task on the execution's loops is cancelled
        (``asyncio.CancelledError`` inside, ``finally`` blocks run); ``exc`` is what
        the worker thread sees when ``run_coro_sync`` returns."""
        if work.finished:
            return False
        loops = [p.loop for p in self.clock.scheduler.parks_for(work)]
        if work.loop is not None and work.loop not in loops:
            loops.append(work.loop)
        if not loops:
            return False
        work.soft_limit_exc = exc

        def _cancel_all(loop):
            for task in asyncio.all_tasks(loop):
                task.cancel(reason)

        for loop in loops:
            try:
                loop.call_soon_threadsafe(_cancel_all, loop)
            except RuntimeError:
                pass
        self.ledger.add("fault", "execution_cancelled", data={"target": work.id, "label": work.label,
                                                              "reason": reason,
                                                              "exception": type(exc).__name__ if exc else "CancelledError"})
        return True

    def in_flight(self) -> list[WorkItem]:
        with self.clock.scheduler.cv:
            return [w for w in self.active if not w.retired()]

    def _abort_in_flight(self, reason: str) -> None:
        """At teardown, abandon and fence whatever is still running or parked."""
        for work in self.in_flight():
            self.crash_execution(work, reason=f"aborted: {reason}", hard=True)


class _BudgetExceeded(RuntimeError):
    pass


class _Submission:
    """The executor as ``run_in_executor`` sees it: records the submitted future."""

    def __init__(self, executor, call: dict):
        self._executor = executor
        self._call = call

    def submit(self, fn, *args):
        token = _owned_pool_submit.set(True)
        try:
            future = self._executor.submit(fn, *args)
        finally:
            _owned_pool_submit.reset(token)
        self._call["future"] = future
        return future


# A pool thread that never exits (frozen with its abandoned execution) must not be
# waited for: by its pool's ``shutdown``, by concurrent.futures' exit hook, or by the
# interpreter's join of non-daemon threads. These are CPython 3.12 internals; without
# them pool work under the engine is refused (UnsupportedFeature), not left to hang.
_EXIT_JOINS_KNOWN = all(hasattr(m, a) for m, a in ((_cf_thread, "_threads_queues"),
                                                     (threading, "_shutdown_locks"),
                                                     (threading, "_shutdown_locks_lock")))


def _detach_from_exit_joins(thread: threading.Thread, executor: Any) -> None:
    # A ThreadPoolExecutor registers a new worker for the exit hook only after
    # ``Thread.start`` returned, inside ``submit`` and under its shutdown lock: taking
    # that lock waits out a submit still in flight (the fence never freezes one).
    # Called without the scheduler's condition, which that submit may need.
    pool_lock = getattr(executor, "_shutdown_lock", None)
    with pool_lock if pool_lock is not None else contextlib.nullcontext():
        threads = getattr(executor, "_threads", None)
        if isinstance(threads, set):
            threads.discard(thread)      # its pool may start a replacement worker
        _cf_thread._threads_queues.pop(thread, None)
    lock = getattr(thread, "_tstate_lock", None)
    if lock is not None:
        with threading._shutdown_locks_lock:
            threading._shutdown_locks.discard(lock)


def _run_coro_sync_importers():
    import sys  # noqa: PLC0415
    for mod in list(sys.modules.values()):
        if hasattr(mod, "run_coro_sync"):
            yield mod


def _brief(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: value[k] for k in ("status", "reason", "action", "imported", "gave_up",
                                      "deferred", "skipped", "candidates", "enqueued")
                if k in value}
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)[:80]
