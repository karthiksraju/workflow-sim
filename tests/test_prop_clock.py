"""Property tests for the simulator's VirtualClock (the simulator itself, not the app).

Mutations these tests were checked against (applied as scratch monkeypatches of
``workflow_sim.clock``, never to the source):

* drop the ``if when <= self._now: return`` guard in ``advance_to``: time moves
  backwards -> ``test_advances_never_move_time_backwards`` fails.
* drop ``self._coordinates.move_to(when)``: ``datetime.now``/``time.time`` stay at
  T0 -> ``test_seam_and_time_machine_agree_after_any_advances`` fails.
* ``_loop_busy`` always ``False`` (jump whenever a sleeper wakes, busy or not):
  the 20s renewer ages the clock mid-write ->
  ``test_background_sleeper_cannot_age_clock_while_foreground_is_runnable`` fails.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import threading

from workflow_sim.clock import VirtualClock
from workflow_sim import time as seam

T0 = datetime(2099, 1, 1, 9, 0, tzinfo=timezone.utc)
SETTINGS = settings(max_examples=100, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])
# Real-time watchdog for the async properties: loop.call_at uses the real
# monotonic clock (not patched), so a livelocked autojump is cut off instead of
# hanging the suite. Virtual sleeps must finish in far less than this.
WATCHDOG_S = 0.5

offsets = st.timedeltas(min_value=timedelta(days=-400), max_value=timedelta(days=400))
zones = st.sampled_from([timezone.utc, timezone(timedelta(hours=5, minutes=30)),
                         timezone(timedelta(hours=-8)), None])


@st.composite
def moves(draw):
    """One advance: ('to', datetime) with any tz (or naive) or ('by', timedelta)."""
    if draw(st.booleans()):
        tz = draw(zones)
        when = T0 + draw(offsets)
        return ("to", when.astimezone(tz).replace(tzinfo=None) if tz is None else when.astimezone(tz))
    return ("by", draw(st.timedeltas(min_value=timedelta(days=-3), max_value=timedelta(days=3))))


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@SETTINGS
@given(st.lists(moves(), max_size=30))
def test_advances_never_move_time_backwards(seq):
    vc = VirtualClock(T0)
    expected = T0
    for kind, value in seq:
        before = vc.now()
        if kind == "to":
            vc.advance_to(value)
            expected = max(expected, _as_utc(value))
        else:
            vc.advance_by(value)
            expected = max(expected, expected + value)
        assert vc.now() >= before
        assert vc.now() == expected
        assert vc.elapsed() >= timedelta(0)
    # every recorded jump is strictly forward and they chain
    for (a, b, _), (c, _d, _r) in zip(vc.jumps, vc.jumps[1:]):
        assert a < b and b == c
    assert all(a < b for a, b, _ in vc.jumps)


@SETTINGS
@given(st.lists(moves(), max_size=20))
def test_seam_and_time_machine_agree_after_any_advances(seq):
    with VirtualClock(T0) as vc:
        for kind, value in seq:
            if kind == "to":
                vc.advance_to(value)
            else:
                vc.advance_by(value)
            now = vc.now()
            assert seam.installed() is vc
            assert seam.now() == now
            assert seam.time() == now.timestamp()
            assert seam.monotonic() == (now - T0).total_seconds()
            assert datetime.now(timezone.utc) == now          # time_machine mirror
            assert abs(time.time() - now.timestamp()) < 1e-5
    assert seam.installed() is None


def _run_with_watchdog(coro_factory, *, wall_timeout=WATCHDOG_S):
    """Run the coroutine in a worker thread; every event loop is on virtual time
    while the clock is installed, so the watchdog must be a real thread timer."""
    box = {}

    def target():
        try:
            box["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(wall_timeout)
    if t.is_alive():
        raise TimeoutError(f"coroutine exceeded {wall_timeout}s wall budget")
    if "error" in box:
        raise box["error"]
    return box.get("value")


durations = st.lists(
    st.one_of(st.just(0.0), st.sampled_from([1.0, 5.0, 20.0, 600.0]),
              st.floats(min_value=0, max_value=86_400, allow_nan=False, allow_infinity=False)),
    min_size=1, max_size=8)


@SETTINGS
@given(durations)
def test_concurrent_sleepers_wake_in_deadline_order_and_clock_ends_at_max(ds):
    """N coroutines sleep concurrently (duplicates and zero included). Each must wake
    at exactly T0+d, in nondecreasing deadline order, the clock must end at the max
    deadline, and it must all take (almost) no wall time."""
    _check_concurrent_sleepers(ds)


# Pinned instances of the property above, so a Hypothesis run that happens not to
# draw them cannot hide a regression. Each was a strict xfail until unit G fixed the
# clock contract (docs/meeting-timeline-simplification.md §9); the cases and the
# property's oracle are unchanged. Before the fix, with a zero-delay sleeper
# runnable in the same step, a timer less than 1us ahead fired at T0 because
# ``VirtualClock.wrap_loop`` set ``loop._clock_resolution = 1e-6`` and asyncio fires
# every timer earlier than ``time() + _clock_resolution``; alone, the same timer
# advanced the clock to T0 + 1us. Stock asyncio on a real clock (resolution ~42ns
# on macOS) woke none of 22,000 such sleepers early. Do not loosen the property to
# make these pass.
@pytest.mark.parametrize("ds", [
    pytest.param([0.0, 8.573802527006959e-07], id="zero+0.857us"),
    pytest.param([0.0, 9.99e-07], id="zero+0.999us"),
    pytest.param([8.573802527006959e-07], id="lone-0.857us"),
    pytest.param([0.0, 5e-07], id="zero+0.5us-rounds-to-T0"),
    pytest.param([0.0, 1.5e-06], id="zero+1.5us"),
    # Found while stating the clock contract (unit G): the same property failed with
    # no zero-delay sleeper. A lone 0.5us or 2.5us sleeper was converted after
    # asyncio's float addition (0.500004us, 2.500019us), not as requested, and a
    # 1.5us sleeper fired inside the 1us window when another timer brought the
    # clock to 1us.
    pytest.param([5e-07], id="lone-0.5us"),
    pytest.param([2.5e-06], id="lone-2.5us"),
    pytest.param([1e-06, 1.5e-06], id="1us+1.5us"),
])
def test_concurrent_sleepers_pinned(ds):
    _check_concurrent_sleepers(ds)


def _check_concurrent_sleepers(ds):
    with VirtualClock(T0) as vc:
        woke = []

        async def sleeper(i, d):
            await asyncio.sleep(d)
            woke.append((i, vc.now()))

        async def all_sleepers():
            await asyncio.gather(*(sleeper(i, d) for i, d in enumerate(ds)))

        wall = time.perf_counter()
        try:
            _run_with_watchdog(all_sleepers)
        except TimeoutError:
            pytest.fail(f"autojump livelocked: sleepers={ds} woke={woke} clock={vc.now()} "
                        f"jumps={len(vc.jumps)} (no progress within {WATCHDOG_S}s wall)")
        elapsed_wall = time.perf_counter() - wall
        deadlines = {i: T0 + timedelta(seconds=d) for i, d in enumerate(ds)}
        assert sorted(i for i, _ in woke) == list(range(len(ds)))
        for i, at in woke:
            assert at == deadlines[i], (i, at, deadlines[i])
        order = [deadlines[i] for i, _ in woke]
        assert order == sorted(order)
        assert vc.now() == max(deadlines.values())
        assert elapsed_wall < WATCHDOG_S


def test_two_concurrent_sleepers_both_wake():
    """Minimal instance of the property above: two parked sleepers. Each polls with
    a real ``sleep(0)``, so each sees the *other's* re-queued handle in
    ``loop._ready`` and ``_loop_busy`` never reports idle."""
    with VirtualClock(T0) as vc:
        woke = []

        async def sleeper(d):
            await asyncio.sleep(d)
            woke.append(vc.now())

        async def both():
            await asyncio.gather(sleeper(5), sleeper(10))

        try:
            _run_with_watchdog(both)
        except TimeoutError:
            pytest.fail(f"two sleepers (5s, 10s) never woke: woke={woke} clock={vc.now()} "
                        f"jumps={vc.jumps}")
        assert woke == [T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)]


def _busy_work(n):
    total = 0
    for i in range(n):
        total += i * i
    return total


async def _plain():            # a coroutine that completes without yielding
    return _busy_work(50)


@SETTINGS
@given(st.lists(st.sampled_from(["sync", "future", "coroutine", "sleep0"]), min_size=1, max_size=25),
       st.integers(min_value=1, max_value=2000))
def test_background_sleeper_cannot_age_clock_while_foreground_is_runnable(steps, work):
    """A 20s background renewer parks while the foreground 'write' runs a series of
    sync steps. Between steps the foreground yields only in ways that leave it
    runnable (a future resolved via call_soon, a coroutine that never suspends,
    a zero sleep) or not at all. The clock must stay at T0 for every step, no
    ``sleep`` jump may be recorded until the foreground is done, and only then
    does the renewer jump to exactly T0+20s."""
    with VirtualClock(T0) as vc:
        seen = []
        renewer_woke = []

        async def renewer():
            await asyncio.sleep(20)
            renewer_woke.append(vc.now())

        async def foreground():
            loop = asyncio.get_running_loop()
            await asyncio.sleep(0)     # let the renewer park first
            for kind in steps:
                _busy_work(work)
                if kind == "future":
                    fut = loop.create_future()
                    loop.call_soon(fut.set_result, None)
                    await fut
                elif kind == "coroutine":
                    await _plain()
                elif kind == "sleep0":
                    await asyncio.sleep(0)
                seen.append((vc.now(), len([j for j in vc.jumps if j[2] == "sleep"])))
            return len(vc.jumps)

        async def main():
            bg = asyncio.ensure_future(renewer())
            jumps_at_done = await foreground()
            await bg
            return jumps_at_done

        try:
            jumps_at_done = _run_with_watchdog(main)
        except TimeoutError:
            pytest.fail(f"did not finish: seen={seen} renewer={renewer_woke} jumps={vc.jumps}")
        assert all(now == T0 and n == 0 for now, n in seen), seen
        assert jumps_at_done == 0
        assert renewer_woke == [T0 + timedelta(seconds=20)]
        assert vc.jumps == [(T0, T0 + timedelta(seconds=20), "autojump")]   # native sleep, virtual loop time
