"""Engine conformance: the contracts the trust audit probed, as regression tests.

Each test is a general timeline-engine property, independent of meeting policy.
"""
import asyncio
import concurrent.futures
import contextlib
import json
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from celery import Celery
from kombu.serialization import dumps, loads

from workflow_sim import runtime as asyncio_compat
from workflow_sim.celery_driver import UnsupportedCeleryFeature, VirtualCelery
from workflow_sim.clock import SimulatedWorkerCrash, VirtualClock
from workflow_sim.engine import Engine
from workflow_sim import time as seam

T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)
APP = Celery("engine-conformance", broker="memory://", backend="cache+memory://")


def sec(n):
    return T0 + timedelta(seconds=n)


# --- ordering across items, tasks and sleepers ------------------------------

def test_timeline_events_run_during_a_virtual_task_sleep():
    """A cancellation scheduled at second 5 must run before a worker that
    sleeps until second 10 resumes (audit: 'world events run during virtual task sleep')."""
    e = Engine(start=T0)
    seen = []
    state = {"cancelled": False}

    def worker():
        async def co():
            await asyncio.sleep(10)
            seen.append(("resume", e.clock.elapsed().total_seconds(), state["cancelled"]))
        asyncio_compat.run_coro_sync(co())

    def cancel():
        state["cancelled"] = True
        seen.append(("cancel", e.clock.elapsed().total_seconds(), True))

    with e:
        e.at(T0, "task", "worker", worker)
        e.at(sec(5), "scenario", "cancel", cancel)
        e.run_until(sec(20))
    assert seen == [("cancel", 5.0, True), ("resume", 10.0, True)]


def test_asyncio_run_inside_a_timeline_callback_is_interleaved_too():
    """Callbacks may drive their own event loop; their sleeps park on the shared scheduler."""
    e = Engine(start=T0)
    seen = []
    with e:
        e.at(T0, "task", "sleeper", lambda: asyncio.run(e.clock.sleep(10)))
        e.at(sec(5), "scenario", "mid", lambda: seen.append(e.clock.elapsed().total_seconds()))
        rep = e.run_until(sec(20))
    assert seen == [5.0]
    assert rep.stop_reason == "horizon" and not rep.in_flight


def test_equal_timestamps_tie_rule_items_before_tasks_then_insertion_order():
    e = Engine(start=T0)
    order = []

    @APP.task(name="conf.tick")
    def tick(label):
        order.append(("task", label))

    with e:
        tick.apply_async(kwargs={"label": "t1"}, eta=sec(5))
        e.at(sec(5), "scenario", "b", lambda: order.append(("item", "b")))
        e.at(sec(5), "scenario", "a", lambda: order.append(("item", "a")))
        tick.apply_async(kwargs={"label": "t2"}, eta=sec(5))
        e.run_until(sec(6))
    assert order == [("item", "b"), ("item", "a"), ("task", "t1"), ("task", "t2")]


# --- horizon, cancellation, timeouts, budgets ----------------------------------

def test_run_until_is_a_hard_horizon_and_reports_in_flight_work():
    e = Engine(start=T0)
    with e:
        e.at(T0, "task", "long", lambda: asyncio.run(e.clock.sleep(100)))
        rep = e.run_until(sec(5))
        assert e.clock.elapsed().total_seconds() == 5
        assert rep.stop_reason == "horizon"
        assert len(rep.in_flight) == 1 and rep.in_flight[0]["deadlines"] == [sec(100).isoformat()]
        rep2 = e.run_until(sec(200))
        assert not rep2.in_flight and e.clock.elapsed().total_seconds() == 200


def test_cancelled_sleepers_do_not_advance_time():
    vc = VirtualClock(T0)

    async def work():
        task = asyncio.create_task(vc.sleep(100))
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await vc.sleep(1)
        for _ in range(5):
            await asyncio.sleep(0)

    with vc:
        asyncio.run(work())
    assert vc.elapsed().total_seconds() == 1, vc.jumps


def test_asyncio_wait_for_obeys_virtual_time():
    vc = VirtualClock(T0)

    async def work():
        try:
            await asyncio.wait_for(vc.sleep(100), timeout=1)
            return "completed"
        except (TimeoutError, asyncio.TimeoutError):
            return "timed_out"

    with vc:
        outcome = asyncio.run(work())
    assert outcome == "timed_out" and vc.elapsed().total_seconds() == 1


def test_wait_for_returns_result_when_it_completes_first():
    vc = VirtualClock(T0)

    async def quick():
        await vc.sleep(1)
        return "ok"

    with vc:
        assert asyncio.run(asyncio.wait_for(quick(), timeout=10)) == "ok"
    assert vc.elapsed().total_seconds() == 1


def test_two_concurrent_sleepers_wake_in_deadline_order():
    vc = VirtualClock(T0)
    woke = []

    async def s(n):
        await vc.sleep(n)
        woke.append(n)

    async def main():
        await asyncio.gather(s(10), s(5), s(5))

    with vc:
        asyncio.run(main())
    assert woke == [5, 5, 10] and vc.elapsed().total_seconds() == 10


def test_step_budget_stops_with_an_explicit_reason():
    e = Engine(start=T0, max_steps=3)
    with e:
        for i in range(10):
            e.at(sec(i), "scenario", f"i{i}", lambda: None)
        rep = e.run_until(sec(100))
    assert rep.stop_reason.startswith("budget")
    assert e.clock.elapsed().total_seconds() == 100   # the horizon is still honoured


# --- Celery semantics ------------------------------------------------------------

def test_expired_tasks_never_execute_effects():
    seen = []

    @APP.task(name="conf.expiry")
    def task():
        seen.append("effect")

    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        task.apply_async(countdown=10, expires=sec(5))
        q.run_all()
    assert seen == [] and q.executed[0].state == "REVOKED"


def test_success_links_and_error_links_run():
    seen = []

    @APP.task(name="conf.source")
    def source():
        return "value"

    @APP.task(name="conf.sink")
    def sink(value):
        seen.append(("ok", value))

    @APP.task(name="conf.boom")
    def boom():
        raise RuntimeError("boom")

    @APP.task(name="conf.err")
    def err(task_id):
        seen.append(("err", bool(task_id)))

    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        source.apply_async(link=sink.s())
        boom.apply_async(link_error=err.s())
        q.run_all()
    assert ("ok", "value") in seen and ("err", True) in seen


def test_payload_encoding_matches_the_configured_kombu_producer():
    content_type, encoding, body = dumps({"when": T0}, serializer="json")
    decoded = loads(body, content_type, encoding)
    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        run = q.push(None, kwargs={"when": T0}, name="fixture")
    assert run.kwargs == decoded


def test_retry_jitter_is_independent_of_ambient_random_state():
    @APP.task(bind=True, name="conf.jitter", autoretry_for=(ValueError,), retry_backoff=30,
              retry_jitter=True, retry_kwargs={"max_retries": 2})
    def task(self):
        if self.request.retries < 2:
            raise ValueError("retry")

    schedules = []
    state = random.getstate()
    try:
        for ambient in (1, 2):
            random.seed(ambient)
            e = Engine(start=T0, seed=7)
            with e:
                task.apply_async()
                e.run_all()
                schedules.append([(r.started_at - T0).total_seconds() for r in e.celery.executed])
    finally:
        random.setstate(state)
    assert schedules[0] == schedules[1] and len(schedules[0]) == 3


def test_priority_orders_equal_due_times():
    order = []

    @APP.task(name="conf.prio")
    def t(label):
        order.append(label)

    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        t.apply_async(kwargs={"label": "low"}, priority=0)
        t.apply_async(kwargs={"label": "high"}, priority=9)
        q.run_all()
    assert order == ["high", "low"]


def test_unsupported_celery_options_are_rejected_explicitly():
    @APP.task(name="conf.plain")
    def plain():
        return 1

    with VirtualClock(T0) as vc, VirtualCelery(vc):
        with pytest.raises(UnsupportedCeleryFeature):
            plain.apply_async(chord="something")


def test_revoked_task_is_skipped():
    seen = []

    @APP.task(name="conf.revoke")
    def t():
        seen.append(1)

    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        res = t.apply_async(countdown=5)
        q.revoke(res.id)
        q.run_all()
    assert seen == [] and q.executed[0].state == "REVOKED"


# --- worker crash, redelivery, duplicates, drops --------------------------------

def test_worker_crash_during_a_parked_sleep_and_acks_late_redelivery():
    attempts = []

    @APP.task(name="conf.crashy")
    def crashy():
        async def body():
            attempts.append(("start", seam.now()))
            await asyncio.sleep(60)
            attempts.append(("end", seam.now()))
        asyncio_compat.run_coro_sync(body())

    e = Engine(start=T0)
    with e:
        run = e.celery.push(crashy, redeliver=True)

        def crash():
            [work] = [w for w in e.in_flight() if w.run is run]
            assert e.crash_execution(work)

        e.at(sec(10), "fault", "crash", crash)
        e.run_until(sec(200))
    states = [(r.state, r.delivery) for r in e.celery.executed]
    assert states == [("CRASHED", "normal"), ("SUCCESS", "redelivered")]
    assert [a[0] for a in attempts] == ["start", "start", "end"]
    assert attempts[1][1] == sec(10)              # redelivered right after the crash
    assert attempts[2][1] == sec(70)


def test_duplicate_delivery_and_dropped_message():
    seen = []

    @APP.task(name="conf.dup")
    def t(label):
        seen.append(label)

    with VirtualClock(T0) as vc, VirtualCelery(vc) as q:
        a = q.push(t, kwargs={"label": "a"}, countdown=5)
        b = q.push(t, kwargs={"label": "b"}, countdown=5)
        q.duplicate(a, delay=1)
        q.drop(b)
        q.run_all()
    assert seen == ["a", "a"]
    assert [r.delivery for r in q.executed] == ["normal", "duplicate"]


# --- determinism and generality -------------------------------------------------

def test_same_seed_same_trace_in_fresh_processes(tmp_path):
    code = """
import asyncio, json, sys
from datetime import datetime, timezone, timedelta
from celery import Celery
from workflow_sim.engine import Engine
from workflow_sim import runtime as asyncio_compat
APP = Celery('det', broker='memory://', backend='cache+memory://')
T0 = datetime(2099,1,1,tzinfo=timezone.utc)
@APP.task(bind=True, name='det.t', autoretry_for=(ValueError,), retry_backoff=30, retry_jitter=True, retry_kwargs={'max_retries': 2})
def t(self, n):
    async def body():
        await asyncio.sleep(n)
    asyncio_compat.run_coro_sync(body())
    if self.request.retries < 2: raise ValueError('again')
e = Engine(start=T0, seed=int(sys.argv[1]))
with e:
    for i in range(3): t.apply_async(kwargs={'n': i+1})
    e.run_all()
print(json.dumps([(r.name, r.retries, (r.started_at-T0).total_seconds(), r.task_id) for r in e.celery.executed]))
"""
    script = tmp_path / "det.py"
    script.write_text(code)
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[3]), "LITELLM_LOCAL_MODEL_COST_MAP": "True",
           "PATH": "/usr/bin:/bin"}
    outs = []
    for _ in range(2):
        outs.append(subprocess.run([sys.executable, str(script), "42"], capture_output=True, text=True,
                                   env=env, timeout=120).stdout.strip().splitlines()[-1])
    other = subprocess.run([sys.executable, str(script), "43"], capture_output=True, text=True,
                           env=env, timeout=120).stdout.strip().splitlines()[-1]
    assert outs[0] == outs[1]
    assert outs[0] != other   # the seed actually reaches jitter and task ids


def test_generic_non_meeting_workload_document_conversion():
    """A queued document-conversion service with duplicate delivery and a lost
    write ACK, run through the same kernel: proves the engine is not meeting-specific."""
    store = {}          # document id -> converted content
    acked = set()       # write acks that reached the worker
    attempts = []

    @APP.task(bind=True, name="docs.convert", autoretry_for=(TimeoutError,), retry_backoff=5,
              retry_jitter=False, retry_kwargs={"max_retries": 3})
    def convert(self, doc_id):
        retries = self.request.retries             # request is thread-local: read it here
        async def body():
            attempts.append((doc_id, retries, seam.now()))
            await asyncio.sleep(2)                 # conversion time
            store[doc_id] = f"converted:{doc_id}"  # the write lands...
            if (doc_id, retries) in lost_acks:
                raise TimeoutError("ack lost")     # ...but the worker never hears back
        asyncio_compat.run_coro_sync(body())
        acked.add(doc_id)

    lost_acks = {("d1", 0)}
    e = Engine(start=T0, seed=1)
    with e:
        r1 = e.celery.push(convert, kwargs={"doc_id": "d1"})
        e.celery.duplicate(r1, delay=1)           # at-least-once broker
        e.celery.push(convert, kwargs={"doc_id": "d2"}, countdown=3)
        rep = e.run_all()
    assert rep.stop_reason == "quiescent"
    assert store == {"d1": "converted:d1", "d2": "converted:d2"}
    assert acked == {"d1", "d2"}
    # d1: both deliveries (original, duplicate) lost their ack on the first attempt
    # and were retried after the 5s backoff; the store saw the write four times.
    d1 = [(retries, (at - T0).total_seconds()) for doc, retries, at in attempts if doc == "d1"]
    assert d1 == [(0, 0.0), (0, 1.0), (1, 7.0), (1, 8.0)]
    assert all(r.state in ("SUCCESS", "RETRY") for r in e.celery.executed)


def test_private_loops_retire_with_their_execution():
    """Each execution's private loop thread exits once the execution finished
    and nothing is scheduled on it. Found by the CalDAV corpus: a 23-day
    timeline (17k steps) leaked one thread per execution until thread
    creation itself blocked and the run never completed. Measured DURING the
    run: teardown stops every loop anyway, so an after-the-run count would
    not see the leak."""
    import threading
    from datetime import datetime, timedelta, timezone
    from workflow_sim import runtime as asyncio_compat
    from workflow_sim.engine import Engine
    T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)
    e = Engine(start=T0)
    seen: list[int] = []

    async def co():
        await asyncio.sleep(0.01)
        return 1

    def work():
        return asyncio_compat.run_coro_sync(co())

    before = threading.active_count()
    with e:
        for i in range(300):
            e.at(T0 + timedelta(seconds=i), "task", f"w{i}", work)
        e.at(T0 + timedelta(seconds=350), "scenario", "count", lambda: seen.append(threading.active_count()))
        rep = e.run_until(T0 + timedelta(seconds=400))
    assert rep.stop_reason == "horizon" and not rep.in_flight
    assert seen and seen[0] - before < 20, f"{seen[0] - before} extra threads alive after 300 executions"


@pytest.mark.parametrize("shape", ["builtin_call_after_c_return", "same_line_store_after_c_return"])
def test_abandoned_thread_blocked_in_a_c_call_never_continues(shape):
    """A hard-abandoned worker blocked in a foreign C call (time.sleep here)
    returns INTO its running Python frame; no Python function is entered, so
    PY_START alone cannot stop it, and a builtin effect (list.append) or a
    plain store on the same line is not a call either. The instruction-level
    fence freezes the very next bytecode. Reviewer holdout crash_c_return_effect."""
    import time as _real
    from datetime import datetime, timezone
    from workflow_sim.engine import Engine as World
    T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)
    seen: list = []
    state: dict = {}

    def builtin_call_after_c_return():
        _real.sleep(0.3)
        seen.append("effect after abandonment")

    def same_line_store_after_c_return():
        state["k"] = (_real.sleep(0.3), "effect after abandonment")[1]

    body = {"builtin_call_after_c_return": builtin_call_after_c_return,
            "same_line_store_after_c_return": same_line_store_after_c_return}[shape]
    w = World(start=T0, max_real_seconds=0.03)
    with w:
        w.at(T0, "task", "blocked_c_call", body)
        report = w.run_until(T0)
    assert report.stop_reason.startswith("budget")
    _real.sleep(0.6)
    assert not seen and not state, (seen, state)


class _SlowHandoffExecutor(concurrent.futures.Executor):
    """Runs the function at once, then waits 0.3 real seconds before publishing the
    result: widens the window between "the pool function returned" and "the result
    was posted to the loop", which is microseconds with a real pool."""

    def submit(self, fn, /, *args, **kwargs):
        import threading  # noqa: PLC0415 — stdlib
        import time as _real  # noqa: PLC0415 — stdlib
        fut = concurrent.futures.Future()

        def work():
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — handed to the future
                _real.sleep(0.3)
                fut.set_exception(exc)
            else:
                _real.sleep(0.3)
                fut.set_result(result)

        threading.Thread(target=work, daemon=True).start()
        return fut


@pytest.mark.parametrize("via", ["to_thread", "run_in_executor", "slow_handoff"])
def test_executor_work_keeps_the_execution_runnable(via):
    """An execution awaiting thread-pool work (asyncio.to_thread /
    loop.run_in_executor) is NOT parked: the clock must not move while the
    work is in flight or its result is on the way back to the loop, and the
    effect it produces lands before later items. Found by the paused
    full-profile unit (real ffprobe work let 900 virtual seconds pass); the run
    also ended INCOMPLETE with the execution in flight."""
    import time as _real
    from datetime import datetime, timedelta, timezone
    from workflow_sim.engine import Engine as World
    T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)
    w = World(start=T0)
    seen: list = []

    def body():
        async def co():
            if via == "to_thread":
                await asyncio.to_thread(_real.sleep, 0.3)
            elif via == "run_in_executor":
                await asyncio.get_running_loop().run_in_executor(None, _real.sleep, 0.3)
            else:
                await asyncio.get_running_loop().run_in_executor(_SlowHandoffExecutor(), lambda: None)
            seen.append(("effect", w.clock.elapsed().total_seconds()))
            await asyncio.sleep(5)
            seen.append(("after-sleep", w.clock.elapsed().total_seconds()))
        asyncio.run(co())

    w.at(T0, "task", "executor_user", body)
    w.at(T0 + timedelta(seconds=1), "scenario", "observer",
         lambda: seen.append(("observer", w.clock.elapsed().total_seconds())))
    with w:
        rep = w.run_until(T0 + timedelta(seconds=10))
    assert [s for s, _ in seen] == ["effect", "observer", "after-sleep"], seen
    assert seen[0][1] == 0.0 and seen[2][1] == 5.0, seen
    assert rep.stop_reason == "horizon" and not rep.in_flight, rep.in_flight


# --- seeded identifiers -------------------------------------------------------

def _ids_under(seed):
    import uuid as uuid_module  # noqa: PLC0415
    from celery.utils import uuid as celery_uuid  # noqa: PLC0415
    e = Engine(start=T0, seed=seed)
    e.install()
    try:
        # uuid4 directly, kombu's uuid() (celery.utils.uuid) and the id a canvas
        # signature receives when a chain freezes it.
        return [str(uuid_module.uuid4()), celery_uuid(), APP.signature("sim.noop").freeze().id]
    finally:
        e.uninstall()


def test_celery_canvas_ids_are_seeded_and_the_default_is_restored():
    import uuid as uuid_module  # noqa: PLC0415
    from kombu.utils.uuid import uuid as kombu_uuid  # noqa: PLC0415
    first = _ids_under(1)
    assert _ids_under(1) == first
    assert _ids_under(2) != first
    assert kombu_uuid.__defaults__ == (uuid_module.uuid4,)           # unpatched after uninstall
    assert kombu_uuid() not in (first[1], _ids_under(1)[1])     # and real again


