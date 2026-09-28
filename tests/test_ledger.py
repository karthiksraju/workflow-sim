"""Ledger entries are immutable evidence; the run snapshot captures content.

Confirmed against 4da6ee33 (scratch worktree): ``Ledger.add`` shallow-copied
``data`` so mutating a nested dict rewrote history, and ``World.summary()`` (the
only run evidence) was identical for rows whose transcripts differed.
"""
import asyncio
import copy
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

from workflow_sim.ledger import Ledger, cause, current_cause
from workflow_sim.engine import Engine as World

T0 = datetime(2099, 1, 1, tzinfo=timezone.utc)


# -- 6. ledger ---------------------------------------------------------------------------

def test_entries_are_snapshots_at_append_time():
    ledger = Ledger(lambda: T0)
    data = {"nested": {"state": "before"}, "items": [1, 2]}
    entry = ledger.add("provider", "reply", data=data)
    data["nested"]["state"] = "after"
    data["items"].append(3)
    assert entry.data == {"nested": {"state": "before"}, "items": [1, 2]}


def test_uncopyable_values_are_frozen_not_aliased():
    ledger = Ledger(lambda: T0)
    lock = threading.Lock()
    entry = ledger.add("log", "x", data={"lock": lock, "n": [1]})
    assert isinstance(entry.data["lock"], str) and "0x" not in entry.data["lock"]


def test_seq_iso_time_and_causal_parent():
    now = [T0]
    ledger = Ledger(lambda: now[0])
    a = ledger.add("scenario", "a")
    with cause("task-123"):
        now[0] = T0 + timedelta(seconds=5)
        b = ledger.add("task", "b")
        token = current_cause.set("inner")
        c = ledger.add("log", "c")
        current_cause.reset(token)
    d = ledger.add("log", "d", causal_parent="explicit")
    assert [e.seq for e in ledger.entries] == [1, 2, 3, 4]
    assert (a.causal_parent, b.causal_parent, c.causal_parent, d.causal_parent) == (
        None, "task-123", "inner", "explicit")
    assert b.at == T0 + timedelta(seconds=5) and b.at_iso == "2099-01-01T00:00:05+00:00"


def test_hash_covers_every_entry_and_to_json_is_canonical():
    def build(value):
        ledger = Ledger(lambda: T0)
        ledger.add("log", "x", data={"v": value, "when": T0, "tags": {"b", "a"}})
        return ledger
    a, b, c = build(1), build(1), build(2)
    assert a.hash() == b.hash() != c.hash()
    assert a.hash() == hashlib.sha256(a.to_json().encode()).hexdigest()
    [record] = json.loads(a.to_json())
    assert record["data"] == {"v": 1, "when": "2099-01-01T00:00:00+00:00", "tags": ["a", "b"]}
    assert record["seq"] == 1 and record["at"] == "2099-01-01T00:00:00+00:00"


