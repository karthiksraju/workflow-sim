"""Property / model-based tests for VirtualCelery (the simulator's task heap).

A pure-Python model keeps ``(due_at, seq, label)`` for every pending task and
predicts which task each ``run_one`` pops, the clock at execution, and what the
task body receives. The real Celery tracer runs every task body.

Mutations these tests were checked against (scratch monkeypatches, not source edits):

* ties broken LIFO instead of FIFO (``seq`` negated) -> the stateful machine and
  ``test_batch_executes_in_due_then_fifo_order`` fail.
* ``run_due`` using ``<`` instead of ``<=`` -> a task due exactly at ``until`` is
  skipped and ``PropCelery.run_due`` fails its model comparison.
* JSON round trip removed from ``push`` -> tuples/int-keyed dicts reach the body
  unchanged and ``test_args_kwargs_survive_json_round_trip`` fails.
"""
import json
from datetime import datetime, timedelta, timezone

from celery import Celery
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (RuleBasedStateMachine, initialize, invariant, precondition,
                                 rule)

from workflow_sim.celery_driver import VirtualCelery
from workflow_sim.clock import VirtualClock
from workflow_sim import time as seam

T0 = datetime(2099, 1, 1, 9, 0, tzinfo=timezone.utc)

app = Celery("sim-prop-celery", broker="memory://", backend="cache+memory://")
app.conf.task_always_eager = False

# Everything the task bodies observe lands here; each example resets it.
LOG: list[dict] = []
# Spawn instructions keyed by label: when the task runs it enqueues a child.
SPAWN: dict[str, dict] = {}


@app.task(name="t.prop_record")
def prop_record(label, payload=None):
    LOG.append({"label": label, "at": seam.now(), "payload": payload})
    child = SPAWN.get(label)
    if child is not None:
        prop_record.apply_async(kwargs={"label": child["label"], "payload": None},
                                **child["timing"])
    return label


@app.task(name="t.prop_positional")
def prop_positional(label, *args, **kwargs):
    LOG.append({"label": label, "at": seam.now(), "args": list(args), "kwargs": kwargs})


def _due(now, timing):
    if timing.get("eta") is not None:
        return timing["eta"]
    if timing.get("countdown") is not None:
        return now + timedelta(seconds=float(timing["countdown"]))
    return now


def timings():
    """countdown / eta / nothing; small integer grid so ties are common, eta may be
    in the past or the future relative to the current clock."""
    minutes = st.integers(min_value=-5, max_value=5)
    return st.one_of(
        st.just({}),
        st.builds(lambda m: {"countdown": max(0, m) * 60}, minutes),
        st.builds(lambda s: {"countdown": s}, st.sampled_from([0, 0.5, 1.25, 60.0])),
        st.builds(lambda m: {"eta_offset": m}, minutes),
    )


def _resolve(timing, now):
    """Turn an ``eta_offset`` (minutes relative to *now*) into an absolute eta."""
    if "eta_offset" in timing:
        return {"eta": now + timedelta(minutes=timing["eta_offset"])}
    return dict(timing)


SETTINGS = settings(max_examples=100, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])


@SETTINGS
@given(st.lists(timings(), min_size=1, max_size=20))
def test_batch_executes_in_due_then_fifo_order(specs):
    LOG.clear(); SPAWN.clear()
    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        expected = []
        for i, spec in enumerate(specs):
            timing = _resolve(spec, vc.now())
            prop_record.apply_async(kwargs={"label": f"t{i}"}, **timing)
            expected.append((_due(vc.now(), timing), i, f"t{i}"))
        assert LOG == []                                   # nothing ran at enqueue
        done = celery.run_all()
        assert [r.kwargs["label"] for r in done] == [lbl for _, _, lbl in sorted(expected)]
        clock = T0
        for entry, (due, _, _lbl) in zip(LOG, sorted(expected)):
            clock = max(clock, due)
            assert entry["at"] == clock
        assert [r.due_at for r in done] == sorted(r.due_at for r in done)
        assert all(r.state == "SUCCESS" for r in done)


json_leaf = st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6),
                      st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=5))
json_like = st.recursive(
    json_leaf,
    lambda kids: st.one_of(st.lists(kids, max_size=3), st.tuples(kids, kids),
                           st.dictionaries(st.one_of(st.text(max_size=3), st.integers(0, 9)),
                                           kids, max_size=3)),
    max_leaves=10)


@SETTINGS
@given(st.lists(json_like, max_size=4),
       st.dictionaries(st.text(min_size=1, max_size=4).filter(lambda k: k != "label"),
                       json_like, max_size=4))
def test_args_kwargs_survive_json_round_trip(args, kwargs):
    LOG.clear(); SPAWN.clear()
    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        prop_positional.apply_async(args=["p", *args], kwargs=kwargs)
        run = celery.run_one()
        assert run.state == "SUCCESS", run.error
        want = json.loads(json.dumps({"args": args, "kwargs": kwargs}))
        assert LOG == [{"label": "p", "at": T0, "args": want["args"], "kwargs": want["kwargs"]}]
        assert run.args == tuple(["p", *want["args"]]) and run.kwargs == want["kwargs"]


class PropCelery(RuleBasedStateMachine):
    """Interleave enqueues (with and without a child spawned from inside the body),
    clock advances, run_one, run_due and run_all; compare against the model."""

    @initialize()
    def setup(self):
        LOG.clear(); SPAWN.clear()
        self.vc = VirtualClock(T0).install()
        self.celery = VirtualCelery(self.vc).install()
        self.model: list[tuple[datetime, int, str]] = []   # pending (due, seq, label)
        self.seq = 0
        self.n = 0
        self.ran: list[tuple[datetime, datetime, str]] = []  # (due, at, label)
        self.logi = 0
        self.mclock = T0                 # the model's own idea of virtual time

    def teardown(self):
        if hasattr(self, "celery"):
            self.celery.uninstall()
            self.vc.uninstall()

    def _model_enqueue(self, due, label):
        self.seq += 1
        self.model.append((due, self.seq, label))

    @rule(spec=timings(), child=st.one_of(st.none(), timings()))
    def enqueue(self, spec, child):
        self.n += 1
        label = f"t{self.n}"
        timing = _resolve(spec, self.vc.now())
        if child is not None:
            # children resolve eta relative to the enqueue time: keep them absolute
            SPAWN[label] = {"label": f"{label}.child", "timing": _resolve(child, self.vc.now())}
        prop_record.apply_async(kwargs={"label": label}, **timing)
        self._model_enqueue(_due(self.vc.now(), timing), label)

    @rule(minutes=st.integers(min_value=0, max_value=10))
    def advance(self, minutes):
        self.vc.advance_by(timedelta(minutes=minutes))
        self.mclock += timedelta(minutes=minutes)

    def _model_pop(self):
        self.model.sort()
        due, _seq, label = self.model.pop(0)
        self.mclock = max(due, self.mclock)     # clock at execution
        return due, label, self.mclock

    def _check_ran(self, run, due, label, at):
        assert run.kwargs["label"] == label, (run.kwargs, label, self.model)
        assert run.due_at == due
        assert run.started_at == at
        entry = LOG[self.logi]           # the body's own observation, in run order
        self.logi += 1
        assert entry["label"] == label and entry["at"] == at, (entry, label, at)
        child = SPAWN.get(label)
        if child is not None:
            self._model_enqueue(_due(at, child["timing"]), child["label"])
        self.ran.append((due, at, label))

    @precondition(lambda self: self.model)
    @rule()
    def run_one(self):
        due, label, at = self._model_pop()
        run = self.celery.run_one()
        self._check_ran(run, due, label, at)

    @rule(minutes=st.integers(min_value=-5, max_value=15))
    def run_due(self, minutes):
        until = self.mclock + timedelta(minutes=minutes)
        done = self.celery.run_due(until)
        expected = 0
        for run in done:
            assert run.due_at <= until, (run.due_at, until)
            due, label, at = self._model_pop()
            self._check_ran(run, due, label, at)
            expected += 1
        # nothing left in the model is due at or before ``until``
        assert all(due > until for due, _, _ in self.model), (until, sorted(self.model))
        assert len(done) == expected

    @rule()
    def run_all(self):
        done = self.celery.run_all()
        for run in done:
            due, label, at = self._model_pop()
            self._check_ran(run, due, label, at)
        assert self.model == [] and self.celery.peek() is None

    @invariant()
    def heap_matches_model(self):
        if not hasattr(self, "celery"):
            return
        pending = [(r.due_at, r.seq, r.kwargs["label"]) for r in self.celery.pending()]
        assert pending == sorted(self.model)

    @invariant()
    def clock_matches_model(self):
        if not hasattr(self, "vc"):
            return
        assert self.vc.now() == self.mclock
        assert seam.now() == self.mclock


PropCelery.TestCase.settings = settings(max_examples=60, stateful_step_count=25, deadline=None,
                                        suppress_health_check=[HealthCheck.too_slow])
TestPropCelery = PropCelery.TestCase


@SETTINGS
@given(st.integers(min_value=0, max_value=6), st.integers(min_value=1, max_value=5))
def test_run_all_terminates_on_chains_of_self_enqueues(depth, fanout):
    """Each root spawns a chain of ``depth`` children; run_all must drain all of them
    (no lost child, no infinite loop) and leave the heap empty."""
    LOG.clear(); SPAWN.clear()
    with VirtualClock(T0) as vc, VirtualCelery(vc) as celery:
        for r in range(fanout):
            for d in range(depth):
                SPAWN[f"r{r}.{d}"] = {"label": f"r{r}.{d + 1}", "timing": {"countdown": 30}}
            prop_record.apply_async(kwargs={"label": f"r{r}.0"})
        done = celery.run_all()
        assert len(done) == fanout * (depth + 1)
        assert celery.peek() is None
        assert vc.now() == T0 + timedelta(seconds=30 * depth)
        # siblings at the same depth run FIFO by root
        assert [r.kwargs["label"] for r in done] == [
            f"r{r}.{d}" for d in range(depth + 1) for r in range(fanout)]
