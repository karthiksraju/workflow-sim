"""Storage boundary conformance: the same SQLite idempotency store behaves
identically under workflow-sim virtual time and under real execution.

The plausible bug each broken control catches: deriving the idempotency key
per attempt (key includes the attempt number), so a retried delivery inserts a
second row. The `exact receipt rows` assertion rejects it in both executions.
"""
import asyncio
import sqlite3
from pathlib import Path

from workflow_sim import run

from adapters.storage_conformance import SqliteStore, fresh_meta, fresh_rows, run_sequence

ADAPTERS = Path(__file__).parent / 'adapters'


def expected_rows():
    return [['evt-old-9', 'in-9', 'cus-7', 300],
            ['in-42', 'in-42', 'cus-7', 1200]]


def expected_broken_rows():
    return [['evt-old-9', 'in-9', 'cus-7', 300],
            ['in-42:0', 'in-42', 'cus-7', 1200],
            ['in-42:1', 'in-42', 'cus-7', 1200]]


def test_sim_fixed_writes_one_row_per_delivery(tmp_path):
    db = str(tmp_path / 'sim-fixed.db')
    result = run('storage_conformance:build', project_dir=ADAPTERS,
                 inputs={'db': db}, duration=10)
    assert result['outcome'] == 'PASS', result
    assert result['evidence']['checks'][0]['actual'] == expected_rows()
    assert result['evidence']['checks'][1]['actual'] == {
        'sqlite_version': sqlite3.sqlite_version}


def test_sim_broken_key_rejected(tmp_path):
    db = str(tmp_path / 'sim-broken.db')
    result = run('storage_conformance:build', project_dir=ADAPTERS,
                 inputs={'db': db, 'broken': True}, duration=10)
    assert result['outcome'] == 'ASSERTION_FAILED', result
    assert result['evidence']['checks'][0]['actual'] == expected_broken_rows()


def test_real_execution_matches_sim(tmp_path):
    sim_broken = run('storage_conformance:build', project_dir=ADAPTERS,
                     inputs={'db': str(tmp_path / 'sim-broken-x.db'), 'broken': True},
                     duration=10)
    assert sim_broken['outcome'] == 'ASSERTION_FAILED', sim_broken
    for broken, want in ((False, expected_rows()), (True, expected_broken_rows())):
        path = str(tmp_path / f'real-{broken}.db')
        store = SqliteStore(path)
        store.store('evt-old-9', 'in-9', 'cus-7', 300)
        asyncio.run(run_sequence(store, broken))
        # Fresh connections prove committed state, not buffered writes.
        assert fresh_rows(path) == want
        assert fresh_meta(path) == {'sqlite_version': sqlite3.sqlite_version}
    assert sim_broken['evidence']['checks'][0]['actual'] == expected_broken_rows()
