"""An execution owns the thread-pool work it submits (``asyncio.to_thread`` and
``loop.run_in_executor``).

Found by the parent review after unit E (``pool-abandonment-holdout.py``): a pool
function that was running when its execution was abandoned (real-time budget,
teardown) appended an effect 300 ms later, after ``World`` had exited. The run
correctly reported INCOMPLETE, so this was not a false PASS. It was a Python effect
outside the per-execution fence the engine advertises.

The rule these tests pin:

* abandonment (hard crash, budget abort, teardown) covers the execution's pool work.
  A running pool function is fenced before its next bytecode, and a queued
  submission never starts;
* graceful cancellation keeps asyncio's semantics. A started pool function runs to
  completion, since threads cannot be cancelled, and the execution stays in flight
  until it has returned, so its effect lands before the engine moves on. A pending
  submission cancelled by the stdlib closes without running and holds nothing open.
  The same holds for a call the body never awaited (fire and forget);
* the fence never freezes a thread inside a simulator critical section (the
  scheduler's condition, a pool ``submit``, which holds ``concurrent.futures``'
  shutdown locks): it freezes as it leaves, so a crash cannot leave the engine or
  the interpreter's exit waiting on a lock held by a frozen thread.

Each case runs in its own interpreter. A frozen pool thread is abandoned for good,
and the case must still exit normally, so a frozen thread must not be joined at
interpreter exit. The effects are appends to a list in that interpreter; nothing
external is touched.
"""
import json
import os
import subprocess
import sys
import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APIS = ["to_thread", "run_in_executor"]

CASE = r'''
import asyncio, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from workflow_sim import runtime as asyncio_compat
from workflow_sim.engine import Engine as World

real_sleep = time.sleep
T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)
case, api = sys.argv[1], sys.argv[2]
seen = []


def submit(fn, executor=None):
    """One pool call through the API under test, as an awaitable."""
    loop = asyncio.get_running_loop()
    if api == "to_thread":
        if executor is not None:
            loop.set_default_executor(executor)
        return asyncio.ensure_future(asyncio.to_thread(fn))
    return loop.run_in_executor(executor, fn)


def effect(name, delay=0.0, started=None):
    def fn():
        if started is not None:
            started.set()
        real_sleep(delay)
        seen.append(name)
    return fn


async def until(event):
    while not event.is_set():          # a zero sleep does not advance virtual time
        await asyncio.sleep(0)


def sync(co):
    # The application's pattern: sync code driving the execution's private loop.
    return lambda: asyncio_compat.run_coro_sync(co())


def observer(w):
    # A later timeline item; records whether the owner's private loop had already
    # retired by then (not only at teardown, which stops every loop anyway).
    def fn():
        seen.append("observer")
        out["owner_loop_retired_at_observer"] = [x.loop_retired for x in w.executions
                                                 if x.label == "pool_owner"]
    return fn


out = {}
if case == "teardown":
    # The reviewer's probe: the real-time budget runs out while the pool body sleeps.
    async def co():
        await submit(effect("pool effect after abandonment", 0.3))
    w = World(start=T0, max_real_seconds=0.04)
    with w:
        w.at(T0, "task", "pool_owner", lambda: asyncio.run(co()))
        report = w.run_until(T0)
elif case == "hard_crash":
    # The hard-crash primitive, invoked while the pool body runs in real time.
    started = threading.Event()
    async def co():
        await submit(effect("pool effect after crash", 0.3, started))
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        def crash():
            started.wait(5)
            target = next(x for x in w.executions if x.label == "pool_owner")
            out["crashed"] = w.crash_execution(target, reason="probe", hard=True)
        threading.Thread(target=crash, daemon=True).start()
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "pending":
    # A submission queued behind a busy one-worker pool when the budget runs out.
    pool = ThreadPoolExecutor(max_workers=1)
    async def co():
        first = submit(effect("running call", 0.3), pool)
        second = submit(effect("queued call"), pool)
        await asyncio.gather(first, second)
    w = World(start=T0, max_real_seconds=0.1)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        report = w.run_until(T0)
    # Outside the engine the pool still works: its frozen worker was replaced, and the
    # queued call of the abandoned execution is dequeued first and skipped.
    pool.submit(effect("pool reusable")).result(timeout=5)
elif case == "cancelled_await":
    # The awaiting task is cancelled while the pool body runs; the body returns at once.
    started = threading.Event()
    async def co():
        call = submit(effect("pool effect", 0.2, started))
        await until(started)
        call.cancel()
        try:
            await call
        except asyncio.CancelledError:
            pass
        seen.append("body returned")
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", observer(w))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "cancelled_pending":
    # A pending submission is cancelled; the stdlib never starts it.
    pool = ThreadPoolExecutor(max_workers=1)
    started = threading.Event()
    async def co():
        first = submit(effect("running call", 0.2, started), pool)
        await until(started)
        second = submit(effect("cancelled call"), pool)
        second.cancel()
        await first
        seen.append("body returned")
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", observer(w))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "fire_and_forget":
    # The body never awaits its pool call.
    async def co():
        submit(effect("pool effect", 0.2))
        seen.append("body returned")
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", observer(w))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "crash_after_return":
    # The body returned without awaiting its pool call; the execution is crashed
    # while that call still runs.
    started = threading.Event()
    async def co():
        submit(effect("pool effect after crash", 0.3, started))
        seen.append("body returned")
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        def crash():
            started.wait(5)
            target = next(x for x in w.executions if x.label == "pool_owner")
            while not target.finished:     # the engine saw the body return
                real_sleep(0)
            out["crashed"] = w.crash_execution(target, reason="probe", hard=True)
        threading.Thread(target=crash, daemon=True).start()
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "teardown_after_return":
    # The body returned without awaiting its pool call; the real-time budget runs
    # out while that call still runs.
    async def co():
        submit(effect("pool effect after abandonment", 0.6))
        seen.append("body returned")
    w = World(start=T0, max_real_seconds=0.2)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        report = w.run_until(T0)
elif case == "own_pool_under_asyncio_run":
    # ``asyncio.run`` returns without waiting for an un-awaited call on a pool of
    # the application's own (it joins only the loop's default executor), so the
    # loop closes before the call's result can be delivered to it.
    pool = ThreadPoolExecutor(max_workers=1)
    async def co():
        submit(effect("pool effect", 0.3), pool)
        seen.append("body returned")
    w = World(start=T0, max_real_seconds=3)
    with w:
        w.at(T0, "task", "pool_owner", lambda: asyncio.run(co()))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", lambda: seen.append("observer"))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "own_pool_teardown_after_close":
    # Loop.close discards its side of the call, but the running pool half must
    # remain owned and fenced if the execution is then abandoned.
    pool = ThreadPoolExecutor(max_workers=1)
    async def co():
        submit(effect("pool effect after abandonment", 0.3), pool)
        seen.append("body returned")
    w = World(start=T0, max_real_seconds=0.04)
    with w:
        w.at(T0, "task", "pool_owner", lambda: asyncio.run(co()))
        report = w.run_until(T0)
elif case == "own_pool_queued_after_close":
    # Neither the running nor the queued call may be lost when their loop closes.
    pool = ThreadPoolExecutor(max_workers=1)
    async def co():
        submit(effect("first pool effect", 0.2), pool)
        submit(effect("second pool effect"), pool)
        seen.append("body returned")
    w = World(start=T0)
    with w:
        w.at(T0, "task", "pool_owner", lambda: asyncio.run(co()))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", lambda: seen.append("observer"))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "armed_crash_with_a_pool_handoff":
    # An armed crash at the execution's first scheduling point belongs to its worker.
    # A pool thread running as the execution (``to_thread`` copies the context) that
    # hands a coroutine to the private loop is not that point. The worker spins
    # without parking, so the trap has no point to fire at in this case.
    from workflow_sim.engine import current_item
    async def small():
        return None
    def fn():
        asyncio_compat.run_coro_sync(small())
        seen.append("pool effect")
    async def co():
        call = submit(fn)
        while not call.done():
            await asyncio.sleep(0)
        seen.append("body saw the result")
    def body():
        current_item.get().crash_at_first_park = "probe"    # what an armed ``worker.crash`` sets
        asyncio.run(co())
    w = World(start=T0, max_real_seconds=3)
    with w:
        w.at(T0, "task", "pool_owner", body)
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "returned_owner_waits_on_engine":
    # After the body returned, its pool call waits on virtual time; the engine must
    # see that call as parked and advance the clock, not wait for it in real time.
    def fn():
        asyncio_compat.run_coro_sync(asyncio.sleep(5))
        seen.append(f"pool effect at +{(w.clock.now() - T0).total_seconds():g}s")
    async def co():
        submit(fn)
        seen.append("body returned")
    w = World(start=T0, max_real_seconds=10)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        w.at(T0 + timedelta(seconds=1), "scenario", "observer", lambda: seen.append("observer"))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "late_pool_call":
    # The body leaves a task behind that makes a pool call two virtual seconds after
    # the body returned: its private loop still runs it, so the execution owns it.
    def fn():
        real_sleep(0.3)
        seen.append(f"late pool call at +{(w.clock.now() - T0).total_seconds():g}s")
    async def later():
        await asyncio.sleep(2)
        await submit(fn)
    async def co():
        asyncio.ensure_future(later())
        seen.append("body returned")
    w = World(start=T0, max_real_seconds=10)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        w.at(T0 + timedelta(seconds=3), "scenario", "observer", lambda: seen.append("observer at +3s"))
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "worker_runs_while_its_pool_call_waits":
    # A pool call waits on virtual time while the worker itself still runs real
    # code: the execution is not parked, so the clock must not move.
    def fn():
        asyncio_compat.run_coro_sync(asyncio.sleep(5))
        seen.append(f"pool effect at +{(w.clock.now() - T0).total_seconds():g}s")
    async def start():
        submit(fn)
    def body():
        asyncio_compat.run_coro_sync(start())
        real_sleep(0.5)
        seen.append(f"worker effect at +{(w.clock.now() - T0).total_seconds():g}s")
    w = World(start=T0, max_real_seconds=10)
    with w:
        w.at(T0, "task", "pool_owner", body)
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "shared_pool":
    # Two executions share one single-worker pool. The first is crashed while its call
    # runs (its crash can land while its loop is still inside ``submit``); the healthy
    # second one then needs the same pool and the same concurrent.futures locks.
    pool = ThreadPoolExecutor(max_workers=1)
    started = threading.Event()
    async def crashed():
        await submit(effect("crashed call", 0.3, started), pool)
    async def healthy():
        await submit(effect("healthy call"), pool)
        seen.append("healthy body returned")
    w = World(start=T0, max_real_seconds=10)
    with w:
        w.at(T0, "task", "pool_owner", sync(crashed))
        w.at(T0 + timedelta(seconds=1), "task", "healthy", sync(healthy))
        def crash():
            started.wait(5)
            target = next(x for x in w.executions if x.label == "pool_owner")
            out["crashed"] = w.crash_execution(target, reason="probe", hard=True)
        threading.Thread(target=crash, daemon=True).start()
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "crash_before_the_body_starts":
    # The call is submitted, its worker thread exists, but the body is held back (the
    # pool's initializer waits): the crash races the submission, then the body is let go.
    gate = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1, initializer=gate.wait)
    async def co():
        await submit(effect("unstarted body ran"), pool)
    w = World(start=T0, max_real_seconds=10)
    with w:
        w.at(T0, "task", "pool_owner", sync(co))
        def crash():
            while not pool._threads:
                real_sleep(0)
            target = next(x for x in w.executions if x.label == "pool_owner")
            out["crashed"] = w.crash_execution(target, reason="probe", hard=True)
            gate.set()
        threading.Thread(target=crash, daemon=True).start()
        report = w.run_until(T0 + timedelta(seconds=10))
elif case == "critical_regions":
    # The fence primitive alone. Each contender is fenced while it waits for the
    # scheduler's condition, then the lock is released to it. Bookkeeping inside the
    # region is harness code (compiled here under a generated file name, as the
    # harness's own generated code is); everything else is application code.
    from workflow_sim.clock import Scheduler, fence
    ns = {}
    exec(compile("""
def nested(sched, seen):
    with sched.cv:
        with sched.cv:
            seen.append("inner bookkeeping")
        seen.append("outer bookkeeping")

def raising(sched, seen):
    with sched.cv:
        seen.append("bookkeeping")
        raise RuntimeError("inside the region")

def calls_back(sched, cb):
    with sched.cv:
        cb()
""", "<harness-bookkeeping>", "exec"), ns)
    class Execution:
        pass
    def app_callback():
        seen.append("application callback ran")
    def nested_contender(sched):
        ns["nested"](sched, seen)
        seen.append("after the region")
    def raising_contender(sched):
        try:
            ns["raising"](sched, seen)
        finally:
            seen.append("finally ran")
    def callback_contender(sched):
        ns["calls_back"](sched, app_callback)
        seen.append("after the region")
    from workflow_sim.clock import VirtualClock
    from workflow_sim.ledger import Ledger
    ledger = Ledger(VirtualClock(T0).now)
    def ledger_contender(sched):
        ledger.add("log", "fenced entry")
        seen.append("after the region")
    for name, contender in (("nested", nested_contender), ("raising", raising_contender),
                            ("callback", callback_contender), ("ledger", ledger_contender)):
        seen.clear()
        sched = Scheduler()
        lock = ledger._lock if name == "ledger" else sched.cv
        lock.acquire()
        t = threading.Thread(target=contender, args=(sched,), daemon=True)
        t.start()
        real_sleep(0.2)                  # blocked on the lock inside the region
        fence(Execution(), {t.ident})
        lock.release()
        real_sleep(0.2)
        free = lock.acquire(timeout=1)
        out[name] = {"effects": list(seen), "lock_free": free}
        if name == "ledger":
            out[name]["entries"] = [e.event_id for e in ledger.entries]
    # An application callback already running inside a region (blocked in a C call)
    # when the fence lands: it freezes at its next instruction, holding the region's
    # lock (the documented boundary), and never continues.
    seen.clear()
    sched = Scheduler()
    entered, gate = threading.Event(), threading.Event()
    def running_callback():
        entered.set()
        gate.wait()
        seen.append("application callback continued")
    def running_contender():
        ns["calls_back"](sched, running_callback)
        seen.append("after the region")
    t = threading.Thread(target=running_contender, daemon=True)
    t.start()
    entered.wait(5)
    real_sleep(0.2)                      # blocked in gate.wait, inside the region
    fence(Execution(), {t.ident})
    gate.set()
    real_sleep(0.2)
    out["callback_running"] = {"effects": list(seen), "lock_free": sched.cv.acquire(timeout=1)}
    print("RESULT " + json.dumps(out), flush=True)
    raise SystemExit(0)
real_sleep(0.7)                          # well past every pool body's real delay
out.update(stop_reason=report.stop_reason, in_flight=[x["label"] for x in report.in_flight],
           abandoned=[x.label for x in w.executions if x.abandoned], effects=list(seen),
           owner_loop_alive=[x.loop_thread.is_alive() for x in w.executions
                             if x.label == "pool_owner" and x.loop_thread is not None],
           pool_abandoned=[e.data for e in w.ledger.entries if e.event_id == "pool_work_abandoned"])
print("RESULT " + json.dumps(out, default=str), flush=True)
'''


def run_case(case, api, tmp_path):
    script = tmp_path / "case.py"
    script.write_text(CASE)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    try:
        proc = subprocess.run([sys.executable, str(script), case, api], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{case}/{api}: the interpreter did not exit (a frozen thread joined at exit?)")
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, (proc.returncode, proc.stdout[-2000:], proc.stderr[-4000:])
    return json.loads(lines[-1][len("RESULT "):])


@pytest.mark.parametrize("api", APIS)
def test_teardown_fences_a_running_pool_call(api, tmp_path):
    r = run_case("teardown", api, tmp_path)
    assert r["stop_reason"].startswith("budget"), r
    assert r["abandoned"] == ["pool_owner"], r
    assert r["effects"] == [], r
    assert [d["running"] for d in r["pool_abandoned"]] == [1], r


@pytest.mark.parametrize("api", APIS)
def test_a_hard_crash_fences_a_running_pool_call(api, tmp_path):
    r = run_case("hard_crash", api, tmp_path)
    assert r["crashed"] is True, r
    assert r["effects"] == [], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [], r
    assert [(d["reason"], d["running"]) for d in r["pool_abandoned"]] == [("probe", 1)], r


@pytest.mark.parametrize("api", APIS)
def test_abandonment_never_starts_a_queued_submission(api, tmp_path):
    r = run_case("pending", api, tmp_path)
    assert r["stop_reason"].startswith("budget"), r
    assert r["effects"] == ["pool reusable"], r
    assert [(d["running"], d["pending"]) for d in r["pool_abandoned"]] == [(1, 1)], r


@pytest.mark.parametrize("api", APIS)
def test_a_cancelled_await_keeps_the_execution_in_flight_until_the_pool_call_returns(api, tmp_path):
    """The pool function still runs (asyncio cannot cancel a thread), but its effect
    lands before the engine moves on to the next item, not at some later virtual time."""
    r = run_case("cancelled_await", api, tmp_path)
    assert r["effects"] == ["body returned", "pool effect", "observer"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [] and r["pool_abandoned"] == [], r
    # The pool half closed last, on the pool thread: its private loop retired once the
    # call closed, before the next item.
    assert r["owner_loop_retired_at_observer"] == [True] and r["owner_loop_alive"] == [False], r


@pytest.mark.parametrize("api", APIS)
def test_a_cancelled_pending_submission_never_runs_and_holds_nothing_open(api, tmp_path):
    r = run_case("cancelled_pending", api, tmp_path)
    assert r["effects"] == ["running call", "body returned", "observer"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [] and r["pool_abandoned"] == [], r
    assert r["owner_loop_retired_at_observer"] == [True] and r["owner_loop_alive"] == [False], r


@pytest.mark.parametrize("api", APIS)
def test_an_unawaited_pool_call_keeps_its_execution_in_flight(api, tmp_path):
    r = run_case("fire_and_forget", api, tmp_path)
    assert r["effects"] == ["body returned", "pool effect", "observer"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [] and r["pool_abandoned"] == [], r
    assert r["owner_loop_retired_at_observer"] == [True] and r["owner_loop_alive"] == [False], r


@pytest.mark.parametrize("api", APIS)
def test_a_hard_crash_after_the_body_returned_fences_its_pool_call(api, tmp_path):
    r = run_case("crash_after_return", api, tmp_path)
    assert r["crashed"] is True, r
    assert r["effects"] == ["body returned"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [], r
    assert [(d["reason"], d["after_return"], d["running"], d["pending"]) for d in r["pool_abandoned"]] == \
        [("probe", True, 1, 0)], r


@pytest.mark.parametrize("api", APIS)
def test_teardown_after_the_body_returned_fences_its_pool_call(api, tmp_path):
    r = run_case("teardown_after_return", api, tmp_path)
    assert r["stop_reason"].startswith("budget"), r
    assert r["abandoned"] == ["pool_owner"], r
    assert r["effects"] == ["body returned"], r
    assert [(d["after_return"], d["running"]) for d in r["pool_abandoned"]] == [(True, 1)], r


@pytest.mark.parametrize("api", APIS)
def test_an_unawaited_call_on_an_own_pool_outlives_asyncio_run(api, tmp_path):
    r = run_case("own_pool_under_asyncio_run", api, tmp_path)
    assert r["effects"] == ["body returned", "pool effect", "observer"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [] and r["abandoned"] == [], r


def test_a_closed_loop_does_not_release_its_running_own_pool_call_on_abandonment(tmp_path):
    r = run_case("own_pool_teardown_after_close", "run_in_executor", tmp_path)
    assert r["stop_reason"].startswith("budget"), r
    assert r["effects"] == ["body returned"], r
    assert r["abandoned"] == ["pool_owner"], r
    assert [(d["running"], d["after_return"]) for d in r["pool_abandoned"]] == [(1, True)], r


def test_a_closed_loop_keeps_queued_own_pool_work_in_flight(tmp_path):
    r = run_case("own_pool_queued_after_close", "run_in_executor", tmp_path)
    assert r["effects"] == ["body returned", "first pool effect", "second pool effect", "observer"], r
    assert r["stop_reason"] == "horizon" and r["in_flight"] == [] and r["abandoned"] == [], r


def test_loop_close_hook_is_restored_after_run_and_failed_install(monkeypatch):
    from workflow_sim.engine import Engine as World
    original = asyncio.BaseEventLoop.close
    original_start = threading.Thread.start
    w = World(start=datetime(2099, 1, 1, tzinfo=timezone.utc))
    with w:
        assert asyncio.BaseEventLoop.close is not original
        assert threading.Thread.start is not original_start
    assert asyncio.BaseEventLoop.close is original
    assert threading.Thread.start is original_start

    w = World(start=datetime(2099, 1, 1, tzinfo=timezone.utc))
    def fail():
        raise RuntimeError("later install stage")
    monkeypatch.setattr(w, "_patch_pool_submit", fail)
    with pytest.raises(RuntimeError, match="later install stage"):
        w.install()
    assert asyncio.BaseEventLoop.close is original
    assert threading.Thread.start is original_start


