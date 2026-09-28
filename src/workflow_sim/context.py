"""Adapter authoring API. Instances are owned by one worker process."""
from __future__ import annotations

import inspect
import json
import math
from datetime import timedelta
from pathlib import Path
from .contracts import encode, json_value
from . import runtime


class Context:
    def __init__(self, engine, inputs: dict, project_dir: Path, scratch_dir: Path):
        self.engine = engine
        self.inputs = inputs
        self.project_dir = project_dir
        self.scratch_dir = scratch_dir
        self._checks = []
        self._labels = set()

    def at(self, seconds: float, label: str, callback):
        """Schedule a sync or async callback at seconds since the scenario start."""
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('event time must be finite nonnegative seconds')
        if not isinstance(label, str) or not label or label in self._labels:
            raise ValueError('event labels must be nonempty and unique')
        if not callable(callback):
            raise ValueError('callback must be callable')
        self._labels.add(label)
        def invoke():
            result = callback()
            if inspect.isawaitable(result):
                return runtime.run_coro_sync(result)
            return result
        self.engine.at(self.engine.start + timedelta(seconds=seconds), 'scenario', label, invoke)

    def expect(self, name: str, actual, expected):
        """Register an exact JSON assertion evaluated after the run horizon."""
        if not isinstance(name, str) or not name or any(c[0] == name for c in self._checks):
            raise ValueError('check names must be nonempty and unique')
        if not callable(actual):
            raise ValueError('actual must be a callable reading final observable state')
        json_value(expected)
        self._checks.append((name, actual, json.loads(encode(expected))))

    def record(self, event: str, **data):
        """Add a causally attributed event; only JSON data is accepted."""
        if not isinstance(event, str) or not event:
            raise ValueError('event must be a nonempty string')
        json_value(data)
        self.engine.ledger.add('application', event, data=data)

    def _evaluate(self):
        checks = []
        for name, actual, expected in self._checks:
            value = actual()
            json_value(value)
            checks.append({'name': name, 'actual': json.loads(encode(value)), 'expected': expected})
        return checks
