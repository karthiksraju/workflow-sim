"""Immutable, causally attributed execution evidence with optional log capture."""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterator, Optional

from workflow_sim.clock import critical
#: What is executing right now (a TaskRun id, a scenario item label, ...). The
#: engine sets it around each unit of work; every entry records it as
#: ``causal_parent``. ``None`` outside any unit of work.
current_cause: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "simulator_current_cause", default=None)


@contextlib.contextmanager
def cause(label: Optional[str]) -> Iterator[None]:
    """Run a block with ``current_cause`` set to ``label``."""
    token = current_cause.set(label)
    try:
        yield
    finally:
        current_cause.reset(token)


_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _snapshot(value: Any) -> Any:
    """A deep copy of ``value`` that later mutation of the original cannot reach.

    Plain data is deep-copied; an object that cannot be deep-copied (a lock, a
    live client, a coroutine) is frozen to its ``repr`` rather than aliased.
    """
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 — uncopyable producer objects are frozen, not aliased
        if isinstance(value, dict):
            return {k: _snapshot(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_snapshot(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_snapshot(v) for v in value)
        return _ADDRESS.sub("", repr(value))


def canonical(value: Any) -> Any:
    """JSON-ready, deterministic form of ``value`` (used for hashing and export)."""
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "len": len(value)}
    return _ADDRESS.sub("", repr(value))


def canonical_json(value: Any) -> str:
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Entry:
    at: datetime
    kind: str            # log | write | task | provider | clock | scenario | fault
    event_id: str
    msg: str = ""
    level: str = "info"
    data: dict = field(default_factory=dict)
    seq: int = 0                          # append order, 1-based, strictly increasing
    causal_parent: Optional[str] = None   # current_cause at append time
    at_iso: str = ""                      # ``at`` as ISO-8601

    def record(self) -> dict:
        """The entry as canonical JSON-ready data."""
        return {"seq": self.seq, "at": self.at_iso or canonical(self.at), "kind": self.kind,
                "event_id": self.event_id, "msg": self.msg, "level": self.level,
                "causal_parent": self.causal_parent, "data": canonical(self.data)}


class Ledger:
    def __init__(self, clock: Callable[[], datetime], *, log_module=None):
        self.log_module = log_module
        self._dispatcher = None
        self.rebound_modules = 0
        self.clock = clock
        self.entries: list[Entry] = []
        self._orig_log_event = None
        self._installed = False
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, kind: str, event_id: str, msg: str = "", *, level: str = "info",
            data: Optional[dict] = None, causal_parent: Optional[str] = None) -> Entry:
        """Append an immutable snapshot. ``data`` is deep-copied now, so the producer
        mutating its dict afterwards does not change the record."""
        frozen = _snapshot(dict(data or {}))
        parent = causal_parent if causal_parent is not None else current_cause.get()
        # A critical section: a fenced thread never freezes holding the ledger.
        with critical, self._lock:
            at = self.clock()
            self._seq += 1
            entry = Entry(at, kind, event_id, str(msg or ""), level, frozen, self._seq, parent,
                          at.isoformat() if hasattr(at, "isoformat") else str(at))
            self.entries.append(entry)
        return entry

    # -- evidence --------------------------------------------------------------
    def records(self) -> list[dict]:
        return [e.record() for e in self.entries]

    def to_json(self) -> str:
        """Canonical JSON (sorted keys, no whitespace) of every entry in append order."""
        return canonical_json(self.records())

    def hash(self) -> str:
        """SHA-256 over :meth:`to_json` (all entries, all fields)."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    # -- capture structured_log.log_event ------------------------------------
    def install(self) -> "Ledger":
        if self._installed:
            return self
        self._installed = True
        if self.log_module is not None:
            self._orig_log_event = self.log_module.log_event
            def dispatch(event_id, msg="", level="info", data=None, exc_info=False):
                if self._installed:
                    self.add("log", event_id, msg, level=level, data=data)
                    return True
                return self._orig_log_event(event_id, msg, level, data, exc_info)
            self._dispatcher = dispatch
            self.rebound_modules = self.rebind()
        return self

    def uninstall(self) -> None:
        self._installed = False
        if self._dispatcher is not None:
            self._replace(self._dispatcher, self._orig_log_event)

    @staticmethod
    def _replace(old, new) -> int:
        import sys
        count = 0
        for module in list(sys.modules.values()):
            if module is not None and getattr(module, "log_event", None) is old:
                try:
                    setattr(module, "log_event", new)
                    count += 1
                except Exception:
                    pass
        return count

    def rebind(self) -> int:
        """Capture aliases imported since install; adapter supplies the log module."""
        if self.log_module is None or not self._installed:
            return 0
        self.log_module.log_event = self._dispatcher
        return self._replace(self._orig_log_event, self._dispatcher)

    # -- queries --------------------------------------------------------------
    def events(self, event_id: Optional[str] = None, *, kind: str = "log") -> list[Entry]:
        return [e for e in self.entries if e.kind == kind
                and (event_id is None or e.event_id == event_id)]

    def has(self, event_id: str) -> bool:
        return any(e.event_id == event_id for e in self.entries)

    def count(self, event_id: str) -> int:
        return sum(1 for e in self.entries if e.event_id == event_id)

    def event_ids(self) -> list[str]:
        return [e.event_id for e in self.entries if e.kind == "log"]

    def render(self, start: datetime, *, include_levels=("info", "warning", "error"),
               kinds=("scenario", "provider", "task", "log", "write")) -> str:
        lines = []
        for e in self.entries:
            if e.kind not in kinds or (e.kind == "log" and e.level not in include_levels):
                continue
            delta = e.at - start
            total = int(delta.total_seconds())
            sign = "-" if total < 0 else "+"
            total = abs(total)
            stamp = f"T{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
            detail = _short(e.data)
            if e.kind == "scenario" and e.msg:
                detail = (e.msg + " " + detail).strip()
            lines.append(f"{stamp}  {e.kind:<8} {e.event_id:<48} {detail}")
        return "\n".join(lines)


def _short(data: dict, limit: int = 110) -> str:
    keep = data
    text = " ".join(f"{k}={_val(v)}" for k, v in keep.items())
    return text[:limit]


def _val(v: Any) -> str:
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}:{_val(x)}" for k, x in list(v.items())[:3]) + "}"
    return str(v)[:40]
