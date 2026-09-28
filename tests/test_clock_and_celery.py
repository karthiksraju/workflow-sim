"""Phase 0 self-tests: the virtual clock and the virtual Celery heap."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from celery import Celery
from celery.exceptions import Retry

from workflow_sim.celery_driver import VirtualCelery
from workflow_sim.clock import VirtualClock
from workflow_sim import time as seam

T0 = datetime(2099, 1, 1, 9, 0, tzinfo=timezone.utc)


def test_seam_follows_virtual_clock_and_time_machine_agrees():
    with VirtualClock(T0) as vc:
        assert seam.now() == T0
        assert datetime.now(timezone.utc) == T0          # time_machine mirror
        assert abs(time.time() - T0.timestamp()) < 1e-6
        vc.advance_by(timedelta(minutes=15))
        assert seam.now() == T0 + timedelta(minutes=15)
        assert datetime.now(timezone.utc) == T0 + timedelta(minutes=15)
        assert seam.monotonic() == 15 * 60
    assert seam.installed() is None
    assert datetime.now(timezone.utc).year != 2099


def test_clock_never_moves_backwards():
    with VirtualClock(T0) as vc:
        vc.advance_to(T0 - timedelta(hours=1))
        assert vc.now() == T0
        vc.advance_to(T0 + timedelta(hours=1))
        vc.advance_to(T0 + timedelta(minutes=30))
        assert vc.now() == T0 + timedelta(hours=1)


def test_asyncio_sleep_autojumps_instead_of_waiting():
    with VirtualClock(T0) as vc:
        async def worker():
            await asyncio.sleep(600)
            return seam.now()
        wall = time.perf_counter()
        woke = asyncio.run(worker())
        assert woke == T0 + timedelta(minutes=10)
        assert vc.now() == woke
        assert time.perf_counter() - wall < 1.0


@pytest.fixture
def app():
    app = Celery("sim-test", broker="memory://", backend="cache+memory://")
    app.conf.task_always_eager = False
    return app


def test_tasks_run_in_due_order_with_countdown_and_eta(app):
    log = []

    @app.task(name="t.record")
    def record(label):
        log.append((label, seam.now()))
        return label

    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        record.apply_async(kwargs={"label": "later"}, countdown=600)
        record.apply_async(kwargs={"label": "now-a"})
        record.apply_async(kwargs={"label": "now-b"})
        record.apply_async(kwargs={"label": "eta"}, eta=T0 + timedelta(minutes=5))
        assert log == []                         # nothing ran at enqueue time
        done = celery.run_due(T0 + timedelta(minutes=5))
        assert [r.name for r in done] == ["t.record"] * 3
        assert [l for l, _ in log] == ["now-a", "now-b", "eta"]
        assert log[2][1] == T0 + timedelta(minutes=5)
        assert celery.peek().kwargs == {"label": "later"}
        celery.run_all()
        assert log[-1] == ("later", T0 + timedelta(minutes=10))
        assert all(r.state == "SUCCESS" for r in celery.executed)


def test_retry_re_enters_heap_at_its_countdown(app):
    attempts = []

    @app.task(bind=True, name="t.flaky", max_retries=3)
    def flaky(self):
        attempts.append(seam.now())
        if len(attempts) < 3:
            raise self.retry(countdown=300)
        return "ok"

    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        flaky.apply_async()
        celery.run_all()
        assert attempts == [T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)]
        assert [r.state for r in celery.executed] == ["RETRY", "RETRY", "SUCCESS"]


def test_autoretry_for_uses_backoff_in_virtual_time(app):
    calls = []

    @app.task(name="t.autoretry", autoretry_for=(ValueError,), retry_backoff=True,
              retry_jitter=False, retry_kwargs={"max_retries": 2})
    def boom():
        calls.append(seam.now())
        if len(calls) < 3:
            raise ValueError("nope")
        return "done"

    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        boom.apply_async()
        celery.run_all()
        assert len(calls) == 3
        assert calls[1] - calls[0] == timedelta(seconds=1)
        assert calls[2] - calls[1] == timedelta(seconds=2)


def test_arguments_are_serialised_like_the_kombu_producer(app):
    """The worker sees exactly what Kombu's configured JSON producer would deliver
    (Kombu's JSON tags datetimes and restores them; tuples become lists)."""
    from kombu.serialization import dumps, loads
    seen = []

    @app.task(name="t.args")
    def echo(when, pair):
        seen.append((when, pair))

    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        echo.apply_async(kwargs={"when": T0, "pair": (1, 2)})
        celery.run_all()
        content_type, encoding, body = dumps({"when": T0, "pair": (1, 2)}, serializer="json")
        expected = loads(body, content_type, encoding)
        assert seen == [(expected["when"], expected["pair"])]
        assert seen[0][1] == [1, 2]


def test_failed_task_is_recorded_not_raised(app):
    @app.task(name="t.fail")
    def fail():
        raise RuntimeError("kaboom")

    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        fail.apply_async()
        run = celery.run_one()
        assert run.state == "FAILURE"
        assert "kaboom" in run.error
