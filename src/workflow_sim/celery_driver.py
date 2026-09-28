"""Virtual Celery: an ordered heap of task deliveries keyed on virtual time.

Celery's ``task_always_eager`` runs a task inline at enqueue time and ignores
``countdown`` / ``eta``; that hides ordering bugs and cannot express "the retry
fires 10 minutes later, after the next poll". This driver records every enqueue
as a :class:`TaskRun` ordered by ``(due_at, -priority, seq)`` and executes tasks
only when the engine pops them.

Supported Celery semantics (each has a conformance test):

* ``countdown`` / ``eta`` (virtual time); ``expires`` (a task popped after its
  expiry is REVOKED and never runs); ``priority`` (higher first at equal time);
* ``retry`` / ``autoretry_for`` with backoff and jitter (jitter is drawn from
  the engine-seeded ``random`` state, so it is reproducible);
* ``link`` (success callbacks receive the result as first argument) and
  ``link_error`` (receive the task id); ``revoke(task_id)``;
* ``chain`` (``celery.chain(...).apply_async()`` sends the first task with the
  remaining signatures in the ``chain`` option; a worker enqueues the next one
  on success, prepending the result unless the signature is immutable --
  ``celery.app.trace._dispatch_callbacks_and_chain``);
* ``soft_time_limit`` / ``time_limit`` measured in virtual time: a task that is
  still running past its soft limit gets ``SoftTimeLimitExceeded`` at its next
  scheduling point, past its hard limit it is crashed;
* ``acks_late`` style redelivery: a crashed task with ``redeliver=True`` is
  re-queued once with the same task id and ``redelivered=True``;
* payloads are serialised with the app's configured Kombu serializer (JSON by
  default), so what the task receives is what a real worker would receive.

Explicitly unsupported (raises :class:`UnsupportedCeleryFeature` at enqueue):
``chord``, ``group`` with a body, ``replace``, ``shadow`` names, rate limits,
``ignore_result`` semantics beyond storing nothing, custom routers.
"""
from __future__ import annotations

import heapq
from contextvars import ContextVar
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from billiard.einfo import ExceptionInfo
from celery import states
from celery.app.task import Task
from celery.app.trace import build_tracer
from celery.exceptions import Retry, SoftTimeLimitExceeded
from celery.result import AsyncResult
from celery.utils.nodenames import gethostname
from kombu.serialization import dumps, loads

from workflow_sim.clock import SimulatedWorkerCrash, VirtualClock


class UnsupportedCeleryFeature(RuntimeError):
    pass


_UNSUPPORTED_OPTIONS = {"chord", "chord_size", "rate_limit", "shadow", "router", "connection",
                        "producer", "publisher", "add_to_parent", "group_id", "group_index"}


@dataclass(order=True)
class TaskRun:
    due_at: datetime
    neg_priority: int
    seq: int
    name: str = field(compare=False)
    task: Any = field(compare=False, repr=False)
    message: tuple = field(compare=False, default=(), repr=False)
    args: tuple = field(compare=False, default=())
    kwargs: dict = field(compare=False, default_factory=dict)
    queue: Optional[str] = field(compare=False, default=None)
    task_id: str = field(compare=False, default="")
    enqueued_at: Optional[datetime] = field(compare=False, default=None)
    countdown: Optional[float] = field(compare=False, default=None)
    expires: Optional[datetime] = field(compare=False, default=None)
    retries: int = field(compare=False, default=0)
    priority: int = field(compare=False, default=0)
    links: list = field(compare=False, default_factory=list)
    error_links: list = field(compare=False, default_factory=list)
    chain: list = field(compare=False, default_factory=list)   # remaining signatures, last first
    soft_time_limit: Optional[float] = field(compare=False, default=None)
    time_limit: Optional[float] = field(compare=False, default=None)
    redeliver: bool = field(compare=False, default=False)
    redelivered: bool = field(compare=False, default=False)
    result: Any = field(compare=False, default=None)
    state: Optional[str] = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    started_at: Optional[datetime] = field(compare=False, default=None)
    finished_at: Optional[datetime] = field(compare=False, default=None)
    delivery: str = field(compare=False, default="normal")   # normal | duplicate | redelivered


class VirtualCelery:
    """Ordered task heap driven by an engine."""

    def __init__(self, clock: VirtualClock, *, seed: int = 0,
                 task_filter: Optional[Callable[[TaskRun], bool]] = None):
        self.clock = clock
        self.seed = seed
        self._heap: list[TaskRun] = []
        self._seq = 0
        self.enqueued: list[TaskRun] = []
        self.executed: list[TaskRun] = []
        self.running: dict[str, TaskRun] = {}
        self.revoked: set[str] = set()
        self.task_filter = task_filter
        self._orig_apply_async = None
        self._installed = False
        self.unsupported: list[str] = []
        self._current_run = ContextVar("celery_run", default=None)
        self._ids = 0
        self._id_rng = random.Random(f"celery-ids:{seed}")
        self.on_run_start: Optional[Callable[[TaskRun], None]] = None
        self.on_run_end: Optional[Callable[[TaskRun], None]] = None

    def reject(self, reason):
        self.unsupported.append(reason)
        raise UnsupportedCeleryFeature(reason)

    # -- enqueue -------------------------------------------------------------
    def _next_id(self) -> str:
        self._ids += 1
        return str(__import__("uuid").UUID(int=self._id_rng.getrandbits(128), version=4))

    def _serializer(self, task) -> str:
        app = getattr(task, "app", None) or getattr(task, "_get_app", lambda: None)()
        conf = getattr(app, "conf", None)
        return (getattr(conf, "task_serializer", None) or "json") if conf is not None else "json"

    def push(self, task, args=(), kwargs=None, *, countdown=None, eta=None, queue=None,
             task_id=None, name=None, retries=0, expires=None, priority=None, link=None,
             link_error=None, chain=None, soft_time_limit=None, time_limit=None, redeliver=False,
             delivery="normal", **options) -> TaskRun:
        unsupported = sorted(k for k in options if k in _UNSUPPORTED_OPTIONS and options[k] is not None)
        if unsupported:
            self.reject(
                f"apply_async option(s) {unsupported} are not modelled by the simulator")
        now = self.clock.now()
        if eta is not None:
            due = eta if eta.tzinfo else eta.replace(tzinfo=timezone.utc)
        elif countdown is not None:
            due = now + timedelta(seconds=float(countdown))
        else:
            due = now
        if isinstance(expires, (int, float)):
            expires = now + timedelta(seconds=float(expires))
        elif isinstance(expires, datetime) and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        # Serialise exactly like a producer would: the worker only ever sees the
        # decoded payload. Unserialisable arguments fail here, at enqueue.
        serializer = self._serializer(task) if task is not None else "json"
        content_type, encoding, body = dumps([list(args or ()), dict(kwargs or {})],
                                             serializer=serializer)
        decoded_args, decoded_kwargs = loads(body, content_type, encoding)
        self._seq += 1
        run = TaskRun(due_at=due, neg_priority=-int(priority or 0), seq=self._seq,
                      name=name or getattr(task, "name", str(task)), task=task,
                      message=(content_type, encoding, body),
                      args=tuple(decoded_args), kwargs=decoded_kwargs, queue=queue,
                      task_id=task_id or self._next_id(), enqueued_at=now, countdown=countdown,
                      expires=expires, retries=int(retries or 0), priority=int(priority or 0),
                      links=_maybe_list(link), error_links=_maybe_list(link_error),
                      chain=list(chain or ()),
                      soft_time_limit=soft_time_limit or getattr(task, "soft_time_limit", None),
                      time_limit=time_limit or getattr(task, "time_limit", None),
                      redeliver=redeliver, delivery=delivery)
        if self.task_filter is not None and not self.task_filter(run):
            run.state = "DROPPED"
            self.enqueued.append(run)
            return run
        heapq.heappush(self._heap, run)
        self.enqueued.append(run)
        return run

    # -- patching -----------------------------------------------------------
    def install(self) -> "VirtualCelery":
        if self._installed:
            return self
        driver = self
        self._orig_apply_async = Task.apply_async
        self._orig_signature_from_request = Task.signature_from_request

        def apply_async(task_self, args=None, kwargs=None, task_id=None, producer=None,
                        link=None, link_error=None, shadow=None, **options):
            if shadow is not None:
                driver.reject("shadow task names are not modelled")
            if producer is not None:
                driver.reject("custom producers are not modelled")
            known = {k: options.pop(k) for k in ("countdown", "eta", "queue", "expires", "priority",
                                                  "soft_time_limit", "time_limit", "retries", "chain")
                     if k in options}
            for ignorable in ("headers", "serializer", "compression", "routing_key", "exchange",
                              "ignore_result", "task_type", "reply_to", "retry", "retry_policy",
                              "stamped_headers", "root_id", "parent_id", "result_cls"):
                options.pop(ignorable, None)
            run = driver.push(task_self, args or (), kwargs or {}, task_id=task_id,
                              link=link, link_error=link_error, **known, **options)
            return AsyncResult(run.task_id, app=task_self.app)

        def signature_from_request(task_self, request=None, args=None, kwargs=None,
                                   queue=None, **extra_options):
            signature = driver._orig_signature_from_request(
                task_self, request=request, args=args, kwargs=kwargs, queue=queue, **extra_options)
            run = driver._current_run.get()
            request = task_self.request if request is None else request
            if run is not None and task_self.name == run.name and request.id == run.task_id:
                # The eager tracer must not dispatch continuations itself; finish()
                # owns that. Restore the suppressed metadata where Celery produces
                # a retry signature, preserving explicit overrides (including None).
                for key, value in (("link", run.links), ("link_error", run.error_links),
                                   ("chain", run.chain)):
                    if key not in extra_options:
                        signature.options[key] = list(value)
            return signature

        Task.signature_from_request = signature_from_request
        Task.apply_async = apply_async
        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return
        Task.apply_async = self._orig_apply_async
        Task.signature_from_request = self._orig_signature_from_request
        self._installed = False

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False

    # -- queue faults --------------------------------------------------------
    def pending(self) -> list[TaskRun]:
        return sorted(self._heap)

    def peek(self) -> Optional[TaskRun]:
        return self._heap[0] if self._heap else None

    def pop(self) -> TaskRun:
        return heapq.heappop(self._heap)

    def find_pending(self, predicate: Callable[[TaskRun], bool]) -> list[TaskRun]:
        return [r for r in self._heap if predicate(r)]

    def drop(self, run: TaskRun) -> None:
        """Message lost in the broker: never delivered."""
        self._heap.remove(run)
        heapq.heapify(self._heap)
        run.state = "DROPPED"

    def duplicate(self, run: TaskRun, *, delay: float = 0.0) -> TaskRun:
        """A second delivery of the same message (at-least-once broker)."""
        self._seq += 1
        copy_ = TaskRun(**{**run.__dict__, "seq": self._seq, "delivery": "duplicate",
                           "due_at": run.due_at + timedelta(seconds=delay),
                           "result": None, "state": None, "error": None,
                           "started_at": None, "finished_at": None})
        heapq.heappush(self._heap, copy_)
        self.enqueued.append(copy_)
        return copy_

    def delay(self, run: TaskRun, seconds: float) -> None:
        """Delivery delayed (reordering relative to later messages)."""
        self._heap.remove(run)
        run.due_at = run.due_at + timedelta(seconds=seconds)
        heapq.heappush(self._heap, run)

    def revoke(self, task_id: str) -> None:
        self.revoked.add(task_id)

    def redeliver(self, run: TaskRun) -> TaskRun:
        """Crashed worker with acks_late: the broker redelivers the message once."""
        self._seq += 1
        again = TaskRun(**{**run.__dict__, "seq": self._seq, "delivery": "redelivered",
                           "redelivered": True, "redeliver": False, "due_at": self.clock.now(),
                           "result": None, "state": None, "error": None,
                           "started_at": None, "finished_at": None})
        heapq.heappush(self._heap, again)
        self.enqueued.append(again)
        return again

    # -- execution (called by the engine in a worker thread) -----------------
    def begin(self, run: TaskRun) -> None:
        run.started_at = self.clock.now()
        self.running[run.task_id] = run
        if self.on_run_start:
            self.on_run_start(run)

    def finish(self, run: TaskRun) -> None:
        run.finished_at = self.clock.now()
        self.running.pop(run.task_id, None)
        self.executed.append(run)
        try:
            if self.on_run_end:
                self.on_run_end(run)
            if run.state == states.SUCCESS:
                for sig in run.links:
                    self._apply_link(sig, (run.result,), run)
                if run.chain:
                    # The worker's chain step (trace._dispatch_callbacks_and_chain): the
                    # next signature is the LAST element; it carries the rest onward.
                    self._apply_link(run.chain[-1], (run.result,), run, chain=run.chain[:-1])
            elif run.state in (states.FAILURE, "CRASHED"):
                for sig in run.error_links:
                    self._apply_errback(sig, run)
                if run.state == "CRASHED" and run.redeliver:
                    self.redeliver(run)
        except Exception as exc:
            # Publication and completion hooks are part of execution health, even
            # when the task body returned successfully. Never hide them in a log.
            run.state = states.FAILURE
            run.error = f"completion_error:{type(exc).__name__}: {exc}"

    def _apply_link(self, sig, extra_args, parent: TaskRun, chain=None) -> None:
        """``link`` semantics (celery.canvas.Signature._merge): a mutable signature
        receives the parent's result prepended to its own args; an immutable
        signature (``task.si(...)``) keeps exactly its own args. ``chain`` is the
        remainder of a chain the enqueued task must carry onward."""
        from celery import signature
        sig = signature(sig, app=parent.task.app)
        options = {} if chain is None else {"chain": chain}
        # Signature.apply_async performs Celery's argument/option merge, then uses
        # the same patched Task.apply_async path as a direct publication.
        sig.apply_async(args=extra_args, **options)

    def _apply_errback(self, sig, parent: TaskRun) -> None:
        """``link_error`` semantics (celery.backends.base._call_task_errbacks): an
        errback whose header takes more than one positional argument is called
        inline with ``(request, exc, traceback)``; an old-style one-argument
        errback is enqueued with the failed task id."""
        from celery.app.task import Context  # noqa: PLC0415 — lazy
        from celery.utils.functional import arity_greater  # noqa: PLC0415 — lazy
        header = getattr(sig.type, "__header__", None)
        if header is not None and arity_greater(header, 1):
            request = Context({"id": parent.task_id, "task": parent.name, "args": list(parent.args),
                               "kwargs": dict(parent.kwargs), "retries": parent.retries,
                               "hostname": gethostname(), "delivery_info": {"routing_key": parent.queue}})
            exc = parent.result if isinstance(parent.result, BaseException) else RuntimeError(parent.error or "failed")
            sig.type(request, exc, parent.error, *(sig.args or ()), **dict(sig.kwargs or {}))
        else:
            self._apply_link(sig, (parent.task_id,), parent)

    def execute(self, run: TaskRun) -> None:
        """Run the task body through Celery's tracer like ``Task.apply`` does, except
        the request is marked non-eager. Eager requests make ``task.retry`` re-run
        the task inline and ignore ``countdown``; a non-eager request makes it call
        ``apply_async`` (our heap) with the retry delay and raise ``Retry``."""
        now = self.clock.now()
        if run.task_id in self.revoked:
            run.state = states.REVOKED
            run.error = "revoked"
            return
        if run.expires is not None and now > run.expires:
            run.state = states.REVOKED
            run.error = f"expired at {run.expires.isoformat()}"
            return
        task = run.task
        app = task._get_app()
        task = app._tasks[task.name]
        request = {
            "id": run.task_id, "task": task.name, "parent_id": None, "root_id": run.task_id,
            "retries": run.retries, "is_eager": False, "logfile": None, "loglevel": 0,
            "hostname": gethostname(), "callbacks": None, "errbacks": None, "headers": None,
            "ignore_result": False, "delivery_info": {"is_eager": False, "exchange": None,
                                                     "routing_key": run.queue, "priority": run.priority,
                                                     "redelivered": run.redelivered},
            "timelimit": (run.time_limit, run.soft_time_limit), "expires": run.expires,
        }
        tracer = build_tracer(task.name, task, eager=True, propagate=False, app=app)
        # Every random draw inside the task body (retry jitter, application code)
        # is a function of the driver seed, the task id and the attempt number,
        # never of the ambient process state.
        random.seed(f"{self.seed}:{run.task_id}:{run.retries}")
        token = self._current_run.set(run)
        try:
            # Each delivery decodes the immutable broker message anew. Task code
            # can mutate its local arguments without changing retries/redelivery.
            content_type, encoding, body = run.message
            args, kwargs = loads(body, content_type, encoding)
            ret = tracer(run.task_id, tuple(args), kwargs, request)
        except SimulatedWorkerCrash as exc:
            run.state = "CRASHED"
            run.error = f"crashed: {exc}"
            return
        finally:
            self._current_run.reset(token)
        retval = ret.retval
        if isinstance(retval, ExceptionInfo):
            retval = retval.exception
            retval = getattr(retval, "exc", retval)
        if isinstance(retval, SimulatedWorkerCrash) or (
                isinstance(retval, BaseException) and isinstance(getattr(retval, "__cause__", None),
                                                                  SimulatedWorkerCrash)):
            run.state = "CRASHED"
            run.error = f"crashed: {retval}"
            return
        state = states.SUCCESS if ret.info is None else ret.info.state
        run.state = state
        if isinstance(retval, Retry):
            run.state = states.RETRY
            run.error = f"retry: {retval.exc!r}"
        elif isinstance(retval, SoftTimeLimitExceeded):
            run.state = states.FAILURE
            run.error = f"soft time limit: {retval!r}"
        elif state == states.SUCCESS:
            run.result = retval
        else:
            run.error = repr(retval)
            run.result = retval

    # -- standalone helpers (no engine; unit tests) --------------------------
    def run_one(self) -> Optional[TaskRun]:
        if not self._heap:
            return None
        run = self.pop()
        self.clock.advance_to(run.due_at, reason=f"task:{run.name}")
        self.begin(run)
        try:
            self.execute(run)
        finally:
            self.finish(run)
        return run

    def run_due(self, until: datetime) -> list[TaskRun]:
        done = []
        until = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
        while self._heap and self._heap[0].due_at <= until:
            done.append(self.run_one())
        return done

    def run_all(self, *, max_runs: int = 10_000) -> list[TaskRun]:
        done = []
        while self._heap:
            done.append(self.run_one())
            if len(done) > max_runs:
                raise RuntimeError("virtual Celery did not drain")
        return done


def _maybe_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]
