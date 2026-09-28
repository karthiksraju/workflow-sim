"""The clock contract: representable time and deadline conversion (unit G).

Contract (docs/meeting-timeline-simplification.md §9):

* Virtual time is whole microseconds since the clock start.
* A delay (``call_later``, ``asyncio.sleep``) is due at ``now + timedelta(seconds=delay)``.
* An absolute loop time ``w`` (``call_at``, ``asyncio.timeout``/``wait_for``) is due at
  the whole microsecond nearest to ``w - 1e6``, ties to even.
* The conversion happens once, at registration. Nothing else (ready callbacks, other
  timers, other loops, the instant a loop waits) changes a timer's due instant.

The oracles are computed here, with datetime arithmetic and exact rational rounding.
They are never read back from the clock. Every test checks where timers actually
fired (the virtual instant a callback saw, the clock's jumps, the run report), not
that a handle was created.
"""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from workflow_sim import runtime as asyncio_compat
from workflow_sim.clock import MAX_ELAPSED_US, ClockRangeError, VirtualClock, range_error
from workflow_sim.engine import Engine
from test_prop_clock import _run_with_watchdog, WATCHDOG_S

T0 = datetime(2099, 1, 1, 9, 0, tzinfo=timezone.utc)
US = timedelta(microseconds=1)
LOOP_EPOCH = 1_000_000.0          # loop.time() at the clock start (asserted below)
ROOT = Path(__file__).resolve().parents[1]

# Delays just below, at and above representable boundaries. The float literals 5e-7
# and 2.5e-6 lie just below their half-microsecond (timedelta rounds them down);
# 0.0078125 s is an exact binary half (7812.5us: ties to even, down) and 0.0234375 s
# is another (23437.5us: ties to even, up).
BOUNDARY_DELAYS = [1e-9, 3e-7, 4.999e-7, 5e-7, 5.0001e-7, 8.573802527006959e-07, 9.99e-07,
                   1e-6, 1.0001e-6, 1.4999e-6, 1.5e-6, 1.5001e-6, 2.5e-6, 0.0078125, 0.0234375,
                   0.25, 20.0]


def at_us(dt, base=T0):
    return (dt - base) // US


def delay_due_us(d):
    """The datetime arithmetic a program would do: now + timedelta(seconds=d)."""
    return timedelta(seconds=d) // US


def absolute_due_us(w):
    """The whole microsecond nearest to w - LOOP_EPOCH, ties to even, exactly."""
    return round((Fraction(w) - Fraction(LOOP_EPOCH)) * 10**6)


def standalone(main, start=T0, elapsed=timedelta(0), *, wall_timeout=WATCHDOG_S):
    """Run ``main(vc)`` on a standalone (autojump) clock under the real-time watchdog,
    after moving the clock ``elapsed`` past its start (jumps recorded after that)."""
    with VirtualClock(start) as vc:
        vc.advance_by(elapsed)
        vc.jumps.clear()
        out = _run_with_watchdog(lambda: main(vc), wall_timeout=wall_timeout)
    return out, vc


def test_loop_time_is_the_representation_the_oracles_assume():
    async def main(vc):
        loop = asyncio.get_running_loop()
        first = loop.time()
        await asyncio.sleep(1e-6)
        return first, loop.time()
    (first, after), _ = standalone(main)
    assert first == LOOP_EPOCH
    assert after == 1 / 1_000_000 + LOOP_EPOCH


# --- delays --------------------------------------------------------------------

async def _ready_stream(n):
    for _ in range(n):
        await asyncio.sleep(0)


CONTEXTS = ["alone", "zero-delay task", "ready stream", "earlier timer at 1us", "registered at +1us"]


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("d", BOUNDARY_DELAYS)
def test_a_delay_is_due_at_now_plus_its_timedelta_whatever_else_is_running(d, context):
    """The same delay wakes at the same datetime-arithmetic instant with nothing else
    running, with a zero-delay task or a stream of ready callbacks, after another
    timer has moved the clock, and when it is requested at a later instant."""
    async def main(vc):
        base = T0
        others = []
        if context == "zero-delay task":
            others.append(asyncio.sleep(0))
        elif context == "ready stream":
            others.append(_ready_stream(25))
        elif context == "earlier timer at 1us":
            others.append(asyncio.sleep(1e-6))
        elif context == "registered at +1us":
            await asyncio.sleep(1e-6)
            base = vc.now()
        woke = []

        async def timer():
            await asyncio.sleep(d)
            woke.append(vc.now())
        await asyncio.gather(timer(), *others)
        return base, woke

    (base, woke), vc = standalone(main)
    assert base == T0 + (US if context == "registered at +1us" else timedelta(0))
    assert woke == [base + timedelta(seconds=d)], (d, context, woke)
    ends = [base + timedelta(seconds=d)] + ([T0 + US] if context == "earlier timer at 1us" else [])
    assert vc.now() == max(ends)


# --- absolute deadlines --------------------------------------------------------

ABSOLUTE_OFFSETS = [5e-7, 8.573802527006959e-07, 1.5e-6, 2.5e-6, 0.0078125, 0.0234375, 3.0]


@pytest.mark.parametrize("how", ["registered at T0", "earlier timer at 1us", "registered at +1us",
                                 "rescheduled at +1us", "zero-delay task"])
@pytest.mark.parametrize("offset", ABSOLUTE_OFFSETS)
def test_an_absolute_deadline_depends_only_on_its_loop_time(offset, how):
    """``call_at(w)`` and ``Timeout.reschedule(w)`` are due at the microsecond nearest
    to ``w``, however and whenever ``w`` reaches the loop."""
    async def main(vc):
        loop = asyncio.get_running_loop()
        w = loop.time() + offset             # the program's own absolute deadline
        fired = []
        if how == "rescheduled at +1us":
            try:
                async with asyncio.timeout(None) as cm:
                    cm.reschedule(w + 3600)
                    await asyncio.sleep(1e-6)
                    cm.reschedule(w)
                    await asyncio.sleep(3600)
            except TimeoutError:
                fired.append(vc.now())
            return w, fired
        if how == "registered at +1us":
            await asyncio.sleep(1e-6)
        done = loop.create_future()
        loop.call_at(w, lambda: (fired.append(vc.now()), done.set_result(None)))
        others = [asyncio.sleep(1e-6)] if how == "earlier timer at 1us" else \
                 [asyncio.sleep(0)] if how == "zero-delay task" else []
        await asyncio.gather(done, *others)
        return w, fired

    (w, fired), _ = standalone(main)
    assert fired == [T0 + absolute_due_us(w) * US], (offset, how, fired)


# --- ties ----------------------------------------------------------------------

def _two_timers(da, db, *, zero=False):
    async def main(vc):
        order = []

        async def s(label, d):
            await asyncio.sleep(d)
            order.append((label, at_us(vc.now())))
        await asyncio.gather(s("a", da), s("b", db), *([asyncio.sleep(0)] if zero else []))
        return order
    order, vc = standalone(main)
    return order, [(at_us(a), at_us(b)) for a, b, _ in vc.jumps]


def test_a_quantization_tie_is_simultaneous_and_behaves_like_an_exact_tie():
    """0.857us and 0.999us are distinct requests with one representable deadline
    (1us): both fire at T0+1us after a single jump, in the same order as two
    requests for exactly 1us. The order within a tie is asyncio's order for equal
    deadlines, which asyncio documents as undefined; it is not asserted here beyond
    being the same as for an exact tie."""
    quantized, q_jumps = _two_timers(8.573802527006959e-07, 9.99e-07)
    exact, e_jumps = _two_timers(1e-6, 1e-6)
    assert sorted(quantized) == [("a", 1), ("b", 1)]
    assert q_jumps == e_jumps == [(0, 1)]
    assert [label for label, _ in quantized] == [label for label, _ in exact]


def test_distinct_representable_deadlines_keep_their_order():
    """0.999us (due 1us) and 1.5us (due 2us) are distinct deadlines: two jumps, in
    deadline order, with or without a zero-delay task, whichever is requested first."""
    for da, db in [(9.99e-07, 1.5e-6), (1.5e-6, 9.99e-07)]:
        for zero in (False, True):
            order, jumps = _two_timers(da, db, zero=zero)
            want = sorted([("a", delay_due_us(da)), ("b", delay_due_us(db))], key=lambda x: x[1])
            assert order == want, (da, db, zero, order)
            assert jumps == [(0, 1), (1, 2)]


def test_a_timer_one_microsecond_ahead_is_never_due_early():
    """At every instant of the first 200us, a 1us sleep registered with a zero-delay
    task alongside wakes exactly one microsecond later. Whether the float loop time
    of the next microsecond is below ``time() + 1e-6`` depends on the instant (it is
    at 7us, 22us, 38us, ...), so a resolution of 1us fires some of them early; half
    a microsecond is always inside the gap."""
    async def main(vc):
        late = []
        for k in range(200):
            await asyncio.gather(asyncio.sleep(1e-6), asyncio.sleep(0))
            if at_us(vc.now()) != k + 1:
                late.append((k, at_us(vc.now())))
                break
        return late
    late, vc = standalone(main)
    assert late == []
    assert at_us(vc.now()) == 200 and len(vc.jumps) == 200


# --- rounded-to-zero delays ----------------------------------------------------

def test_a_delay_that_rounds_to_zero_yields_once_and_does_not_advance_time():
    """sleep(3e-7) is due at the current instant: the sleeper waits one loop pass
    (other ready callbacks run first), then continues. No jump is recorded."""
    async def main(vc):
        events = []

        async def tiny():
            for i in range(3):
                await asyncio.sleep(3e-7)
                events.append(("tiny", i, at_us(vc.now())))

        async def zero():
            for i in range(3):
                await asyncio.sleep(0)
                events.append(("zero", i, at_us(vc.now())))
        await asyncio.gather(tiny(), zero())
        # Catch a wrong deadline before running the throughput stress tail.
        assert all(at == 0 for *_, at in events) and vc.jumps == []
        for _ in range(10_000):              # and many in a row finish promptly
            await asyncio.sleep(1e-7)
        return events

    # The 10,000-iteration stress check is not a 500ms throughput promise.
    # Keep exact time/order assertions; allow slower shared CI hosts.
    events, vc = standalone(main, wall_timeout=5)
    assert all(at == 0 for *_, at in events) and vc.jumps == []
    labels = [e[0] for e in events]
    assert labels.index("zero") < len(labels) - 1 - labels[::-1].index("tiny"), events  # tiny yielded


POLLER = textwrap.dedent('''
    import asyncio, json
    from datetime import datetime, timezone
    from workflow_sim import runtime as asyncio_compat
    from workflow_sim.engine import Engine
    T0 = datetime(2099, 1, 1, 9, 0, tzinfo=timezone.utc)
    e = Engine(start=T0, max_real_seconds=2)
    async def poll():
        loop = asyncio.get_running_loop()
        end = loop.time() + 1e-3
        while loop.time() < end:
            await asyncio.sleep(1e-7)     # rounds to zero: never idle, time never moves
    with e:
        e.at(T0, "task", "poller", lambda: asyncio_compat.run_coro_sync(poll()))
        rep = e.run_until(datetime(2099, 1, 1, 9, 0, 1, tzinfo=timezone.utc))
        print("RESULT " + json.dumps({"stop": rep.stop_reason, "in_flight": [w["label"] for w in rep.in_flight],
                                      "jumps": [[str(a), str(b), r] for a, b, r in e.clock.jumps]}), flush=True)
''')


def test_polling_time_with_zero_rounded_sleeps_fails_closed_under_the_engine(tmp_path):
    """The consequence the contract states: a loop that waits for time to pass with
    sub-half-microsecond sleeps is never idle, so the engine never advances the
    clock and the run ends at the real-time budget with the poller in flight
    (INCOMPLETE), not at the horizon and not with time moved."""
    script = tmp_path / "poller.py"
    script.write_text(POLLER)
    proc = subprocess.run([sys.executable, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
    r = json.loads(lines[-1][len("RESULT "):])
    assert r["stop"].startswith("budget: real time budget exhausted"), r
    assert r["in_flight"] == ["poller"]
    # run_until moves the clock to its horizon when it stops; before that nothing did
    assert [j[2] for j in r["jumps"]] == ["run_until"], r


# --- timeouts and cancellation -------------------------------------------------

def _wait_for(a, b, *, zero=False):
    async def main(vc):
        loop = asyncio.get_running_loop()
        w = loop.time() + b
        extra = asyncio.ensure_future(asyncio.sleep(0)) if zero else None
        try:
            await asyncio.wait_for(asyncio.sleep(a), b)
            outcome = "result"
        except TimeoutError:
            outcome = "timeout"
        if extra is not None:
            await extra
        return outcome, at_us(vc.now()), w
    return standalone(main)[0]


@pytest.mark.parametrize("zero", [False, True], ids=["alone", "zero-delay task"])
@pytest.mark.parametrize("a,b", [(1e-6, 2e-6), (2e-6, 1e-6), (8.573802527006959e-07, 1.5e-6),
                                 (1.5e-6, 8.573802527006959e-07), (2e-6, 8.573802527006959e-07),
                                 (5.0, 20.0), (20.0, 5.0)])
def test_wait_for_resolves_at_the_earlier_representable_deadline(a, b, zero):
    """Distinct representable deadlines: the earlier one decides, at its own instant,
    whatever else is ready. (At 23e03da0, sleep 0.857us / timeout 1.5us timed out.)"""
    outcome, at, w = _wait_for(a, b, zero=zero)
    sleep_due, timeout_due = delay_due_us(a), absolute_due_us(w)
    assert sleep_due != timeout_due
    assert (outcome, at) == (("result", sleep_due) if sleep_due < timeout_due else ("timeout", timeout_due))


def test_wait_for_on_a_quantization_tie_behaves_like_an_exact_tie():
    """sleep 0.857us with timeout 0.999us: one representable instant (1us). The
    outcome is whatever an exact tie (1us, 1us) gives. In 3.12 ``wait_for`` registers
    its timeout first, so an exact tie times out, as stock asyncio does when both
    deadlines are equal."""
    for zero in (False, True):
        tie = _wait_for(8.573802527006959e-07, 9.99e-07, zero=zero)[:2]
        exact = _wait_for(1e-6, 1e-6, zero=zero)[:2]
        assert tie == exact == ("timeout", 1), (zero, tie, exact)


def test_sleep_and_timeout_of_the_same_near_half_delay_differ_as_documented():
    """The stated policy consequence: asyncio builds ``timeout(d)`` as the absolute
    ``loop.time() + d``, so for the literal 2.5e-6 (just below a half microsecond
    as a float, just above it after adding 1e6) the sleep is due at +2us and the
    timeout at +3us. Pinned so any change to either conversion is seen."""
    outcome, at, w = _wait_for(2.5e-6, 2.5e-6)
    assert (delay_due_us(2.5e-6), absolute_due_us(w)) == (2, 3)
    assert (outcome, at) == ("result", 2)


def test_a_cancelled_normalized_timer_never_fires_and_never_moves_the_clock():
    async def main(vc):
        loop = asyncio.get_running_loop()
        fired = []
        h1 = loop.call_later(8.573802527006959e-07, fired.append, "0.857us")   # due 1us
        h0 = loop.call_later(3e-7, fired.append, "0.3us")                      # due now
        h1.cancel()
        h0.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(5)
        return fired
    fired, vc = standalone(main)
    assert fired == []
    assert [(at_us(a), at_us(b), r) for a, b, r in vc.jumps] == [(0, 5_000_000, "autojump")]


# --- several loops under the engine --------------------------------------------

def _loops(start, delays, *, horizon):
    """One execution per (label, delay), started in list order at ``start``; each
    drives its own loop. Returns wake order with microsecond offsets and the report."""
    e = Engine(start=start)
    woke = []

    def ex(label, d):
        async def co():
            await asyncio.sleep(d)
            woke.append((label, at_us(e.clock.now(), start)))
        return lambda: asyncio_compat.run_coro_sync(co())

    with e:
        for label, d in delays:
            e.at(start, "task", label, ex(label, d))
        rep = e.run_until(horizon(e))
    return woke, rep, e


@pytest.mark.parametrize("year", [2099, 3000, 9000])
def test_parked_loops_wake_in_exact_deadline_order_at_any_date(year):
    """A parks first and is due later. At 23e03da0, from year 3000 the 3us loop woke
    at 4us after the 4us one: parks were ordered by a timestamp float."""
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    woke, _, _ = _loops(start, [("A", 4e-6), ("B", 3e-6), ("C", 1.5e-6)],
                        horizon=lambda e: start + timedelta(seconds=1))
    assert woke == [("C", 2), ("B", 3), ("A", 4)]


def test_loops_with_one_representable_deadline_wake_one_at_a_time_in_park_order():
    """0.999us and 0.857us in two loops share the deadline 1us: the engine wakes
    them one at a time in park order (A parked first), both at +1us."""
    woke, _, e = _loops(T0, [("A", 9.99e-07), ("B", 8.573802527006959e-07)],
                        horizon=lambda e: T0 + timedelta(seconds=1))
    assert woke == [("A", 1), ("B", 1)]
    assert [(at_us(a), at_us(b), r) for a, b, r in e.clock.jumps][:1] == [(0, 1, "timer")]


def test_a_hard_horizon_between_a_request_and_its_representable_deadline():
    """1.5us is due at 2us: a horizon at 1us leaves it in flight and the clock at
    exactly 1us; a later horizon wakes it at 2us. 0.857us (due 1us) wakes by the
    first horizon, also with a zero-delay task in its loop."""
    e = Engine(start=T0)
    woke = []

    def ex(label, d, zero=False):
        async def co():
            await asyncio.gather(asyncio.sleep(d), *([asyncio.sleep(0)] if zero else []))
            woke.append((label, at_us(e.clock.now())))
        return lambda: asyncio_compat.run_coro_sync(co())

    with e:
        e.at(T0, "task", "late", ex("late", 1.5e-6))
        e.at(T0, "task", "early", ex("early", 8.573802527006959e-07, zero=True))
        first = e.run_until(T0 + US)
        clock_at_first = at_us(e.clock.now())
        second = e.run_until(T0 + 2 * US)
    assert first.stop_reason == "horizon" and [w["label"] for w in first.in_flight] == ["late"]
    assert clock_at_first == 1
    assert second.stop_reason == "horizon" and not second.in_flight
    assert woke == [("early", 1), ("late", 2)]


# --- stock asyncio, where it is a meaningful reference -------------------------

def test_distinct_deadlines_fire_in_the_same_order_as_stock_asyncio():
    """Stock asyncio on the real clock orders distinct deadlines by the heap, not by
    wake jitter, so the order (not the instant) is an exact reference. Deadlines are
    milliseconds apart; a zero-delay task runs alongside in both."""
    delays = [0.003, 0.0, 0.001, 0.004, 0.002, 0.0015]

    async def run(now):
        order = []

        async def s(i, d):
            await asyncio.sleep(d)
            order.append((i, now()))
        await asyncio.gather(*(s(i, d) for i, d in enumerate(delays)), asyncio.sleep(0))
        return order

    stock = asyncio.run(run(time.monotonic))
    virtual, _ = standalone(lambda vc: run(vc.now))
    assert [i for i, _ in virtual] == [i for i, _ in stock]
    assert [at for _, at in virtual] == [T0 + timedelta(seconds=delays[i]) for i, _ in virtual]


# --- properties ----------------------------------------------------------------

def _near_boundaries():
    half = st.integers(min_value=0, max_value=40).map(lambda k: k * 5e-7)
    nudge = st.sampled_from([0.0, 1e-13, -1e-13, 1e-10, -1e-10, 3e-8, -3e-8])
    return st.builds(lambda h, n: max(0.0, h + n), half, nudge)


DELAYS = st.one_of(_near_boundaries(), st.just(0.0), st.floats(min_value=0, max_value=1e-4),
                   st.floats(min_value=0, max_value=86_400, allow_nan=False, allow_infinity=False))
PROP = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROP
@given(st.lists(DELAYS, min_size=1, max_size=6), st.integers(min_value=0, max_value=3),
       st.integers(min_value=0, max_value=30))
def test_prop_each_delay_wakes_at_its_own_datetime_deadline_whatever_else_is_ready(ds, zeros, stream):
    """Concurrent sleepers plus zero-delay tasks and a ready stream: each sleeper
    wakes exactly at T0 + timedelta(seconds=d), in nondecreasing deadline order,
    and the clock ends at the latest deadline."""
    async def main(vc):
        woke = []

        async def s(i, d):
            await asyncio.sleep(d)
            woke.append((i, vc.now()))
        await asyncio.gather(*(s(i, d) for i, d in enumerate(ds)), *(asyncio.sleep(0) for _ in range(zeros)),
                             _ready_stream(stream))
        return woke

    woke, vc = standalone(main)
    due = {i: T0 + timedelta(seconds=d) for i, d in enumerate(ds)}
    assert sorted(i for i, _ in woke) == list(range(len(ds)))
    assert all(at == due[i] for i, at in woke), (ds, woke)
    assert [due[i] for i, _ in woke] == sorted(due[i] for i, _ in woke)
    assert vc.now() == max(due.values())


@PROP
@given(st.lists(st.tuples(DELAYS, st.integers(min_value=0, max_value=3)), min_size=1, max_size=5))
def test_prop_an_absolute_deadline_is_due_at_its_nearest_microsecond_wherever_registered(reqs):
    """Each request is an absolute loop time taken at T0 (``t0 + offset``) and
    registered after ``k`` 1us steps. Registration instant and neighbours do not
    change its due instant, except that one already due fires at registration."""
    async def main(vc):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        fired = {}

        async def reg(i, offset, k):
            for _ in range(k):
                await asyncio.sleep(1e-6)
            registered = at_us(vc.now())
            done = loop.create_future()
            loop.call_at(t0 + offset, lambda: (fired.setdefault(i, at_us(vc.now())), done.set_result(None)))
            await done
            return registered, t0 + offset
        regs = await asyncio.gather(*(reg(i, o, k) for i, (o, k) in enumerate(reqs)))
        return fired, regs

    (fired, regs), _ = standalone(main)
    for i, (registered, w) in enumerate(regs):
        assert fired[i] == max(registered, absolute_due_us(w)), (reqs[i], registered, fired[i])


# --- what the conversion leaves native -----------------------------------------

def test_an_unrepresentable_deadline_keeps_asyncio_behaviour():
    """``inf`` and delays beyond the timedelta range keep asyncio's native float
    deadline: they are never due, they can still be cancelled, and they do not
    disturb the representable timers around them. ``None`` is still rejected."""
    async def main(vc):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.call_later(5, fut.set_result, "done")
        got = await asyncio.wait_for(fut, float("inf"))          # timeout(inf) -> call_at(inf)
        at_result = at_us(vc.now())
        huge = asyncio.ensure_future(asyncio.sleep(1e300))        # beyond timedelta
        loop.call_later(1, huge.cancel)
        try:
            await huge
            huge_outcome = "finished"
        except asyncio.CancelledError:
            huge_outcome = "cancelled"
        except Exception as exc:  # noqa: BLE001 — the outcome is asserted below
            huge_outcome = f"raised {type(exc).__name__}"
        with pytest.raises(TypeError):
            loop.call_later(None, lambda: None)
        return got, at_result, huge_outcome, at_us(vc.now())

    out, vc = standalone(main)
    assert out == ("done", 5_000_000, "cancelled", 6_000_000)
    assert [(at_us(a), at_us(b)) for a, b, _ in vc.jumps] == [(0, 5_000_000), (5_000_000, 6_000_000)]


def test_debug_source_tracebacks_still_end_at_the_caller():
    """The conversion wrappers do not appear in a debug-mode handle's traceback."""
    async def main(vc):
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        handles = [loop.call_later(1, lambda: None), loop.call_at(loop.time() + 1, lambda: None)]
        frames = [h._source_traceback[-1] for h in handles]
        for h in handles:
            h.cancel()
        return [(Path(f.filename).name, f.name) for f in frames]

    frames, _ = standalone(main)
    assert frames == [("test_clock_contract.py", "main")] * 2


# --- the supported elapsed range -----------------------------------------------

MAX_ELAPSED = timedelta(microseconds=MAX_ELAPSED_US)


def test_the_supported_range_is_about_34_years_of_elapsed_time():
    """Stated in clock.py and §9: loop.time() up to and including 2**30 s."""
    assert MAX_ELAPSED_US / 1_000_000 + 1_000_000 == 2**30
    assert range_error(T0, T0 + timedelta(microseconds=MAX_ELAPSED_US)) is None
    assert range_error(T0, T0 + timedelta(microseconds=MAX_ELAPSED_US + 1)) is not None
    assert timedelta(days=365 * 33) < MAX_ELAPSED < timedelta(days=365 * 35)


@pytest.mark.parametrize("elapsed", [timedelta(days=30), timedelta(days=365), timedelta(days=365 * 10),
                                     MAX_ELAPSED - 2 * US],
                         ids=["+30 days", "+1 year", "+10 years", "range end - 2us"])
def test_microsecond_timers_are_exact_after_a_long_elapsed_time(elapsed):
    """Meeting-scale (and range-end) elapsed time: a 1us sleep wakes 1us later, a
    0.857us sleep with a zero-delay task wakes 1us later, and an absolute deadline
    one microsecond ahead fires then. (Start dates do not test this: loop time is
    measured from the clock start.)"""
    async def main(vc):
        loop = asyncio.get_running_loop()
        base = vc.now()
        await asyncio.sleep(1e-6)
        a = vc.now() - base
        await asyncio.gather(asyncio.sleep(8.573802527006959e-07), asyncio.sleep(0))
        b = vc.now() - base
        if vc.now() - vc.start < MAX_ELAPSED:
            done = loop.create_future()
            loop.call_at(loop.time() + 1e-6, done.set_result, None)
            await done
        return a, b, vc.now() - base

    (a, b, c), _ = standalone(main, elapsed=elapsed)
    assert (a, b, c) == (US, 2 * US, 2 * US if elapsed == MAX_ELAPSED - 2 * US else 3 * US)


def test_the_clock_fails_explicitly_beyond_the_supported_range():
    """At the range end a timer one microsecond beyond it cannot be represented:
    it keeps asyncio's native deadline, and waiting for it raises ClockRangeError
    at once instead of livelocking. Advancing past the range raises and leaves the
    clock where it was; the reviewer's 2**33 case is refused the same way."""
    async def main(vc):
        await asyncio.sleep(1e-6)                 # due exactly at the range end: fine
        at_end = vc.now() - vc.start
        await asyncio.sleep(1e-6)                 # due beyond it
        return at_end

    with pytest.raises(ClockRangeError):
        standalone(main, elapsed=MAX_ELAPSED - US)
    with VirtualClock(T0) as vc:
        vc.advance_by(MAX_ELAPSED)
        assert vc.now() == T0 + MAX_ELAPSED
        with pytest.raises(ClockRangeError):
            vc.advance_by(US)
        assert vc.now() == T0 + MAX_ELAPSED
    with VirtualClock(T0) as vc:
        with pytest.raises(ClockRangeError):
            vc.advance_by(timedelta(seconds=2**33 - 1_000_000))
        assert vc.now() == T0 and vc.jumps == []


def test_a_range_end_timer_fires_at_the_range_end():
    """Positive control for the boundary itself: a sleep due exactly at the range
    end wakes there (the test above raises only for the next microsecond)."""
    async def main(vc):
        await asyncio.sleep(1e-6)
        return vc.now() - vc.start
    at, _ = standalone(main, elapsed=MAX_ELAPSED - US)
    assert at == MAX_ELAPSED


def test_an_engine_horizon_beyond_the_supported_range_fails_explicitly():
    e = Engine(start=T0)
    with e:
        with pytest.raises(ClockRangeError):
            e.run_until(T0 + MAX_ELAPSED + US)
        assert e.clock.now() == T0
        report = e.run_until(T0 + MAX_ELAPSED)
    assert report.stop_reason == "horizon" and e.clock.now() == T0 + MAX_ELAPSED




def test_zero_delay_assertions_detect_a_one_microsecond_timer_mutation(monkeypatch):
    """Negative control: delaying every normalized timer must fail the time oracle."""
    from workflow_sim import clock
    original = clock._real_call_at
    def delayed(loop, when, callback, *args, context=None):
        return original(loop, when + 1e-6, callback, *args, context=context)
    monkeypatch.setattr(clock, '_real_call_at', delayed)
    with pytest.raises(AssertionError):
        test_a_delay_that_rounds_to_zero_yields_once_and_does_not_advance_time()


def test_watchdog_still_rejects_a_stalled_coroutine(tmp_path):
    source = '''import asyncio, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_prop_clock import _run_with_watchdog
try:
 _run_with_watchdog(lambda: asyncio.Event().wait())
except TimeoutError:
 pass
else:
 raise AssertionError('watchdog accepted a stalled coroutine')
'''
    process = subprocess.run([sys.executable, '-c', source, str(Path(__file__).parent)],
                             capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
