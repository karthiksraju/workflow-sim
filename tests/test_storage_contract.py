"""Storage boundary conformance: the same SQLite idempotency store behaves
identically under workflow-sim virtual time and under real execution.

The plausible bug each broken control catches: deriving the idempotency key
per attempt (key includes the attempt number), so a retried delivery inserts a
second row. The `exact receipt rows` assertion rejects it in both executions.
"""
import asyncio
from pathlib import Path

from workflow_sim import run

from adapters.storage_conformance import SqliteStore, run_sequence

ADAPTERS = Path(__file__).parent / 'adapters'


def expected_rows():
    return [['evt-old-9', 'in-9', 'cus-7', 300],
            ['in-42', 'in-42', 'cus-7', 1200]]


def test_sim_fixed_writes_one_row_per_delivery(tmp_path):
    db = str(tmp_path / 'sim-fixed.db')
    result = run('storage_conformance:build', project_dir=ADAPTERS,
                 inputs={'db': db}, duration=10)
    assert result['outcome'] == 'PASS', result
    assert result['evidence']['checks'][0]['actual'] == expected_rows()


def test_sim_broken_key_rejected(tmp_path):
    db = str(tmp_path / 'sim-broken.db')
    result = run('storage_conformance:build', project_dir=ADAPTERS,
                 inputs={'db': db, 'broken': True}, duration=10)
    assert result['outcome'] == 'ASSERTION_FAILED', result
    assert result['evidence']['checks'][0]['actual'] != expected_rows()
    assert len(result['evidence']['checks'][0]['actual']) == 3


def test_real_execution_matches_sim(tmp_path):
    for broken, want in ((False, expected_rows()), (True, None)):
        store = SqliteStore(str(tmp_path / f'real-{broken}.db'))
        store.store('evt-old-9', 'in-9', 'cus-7', 300)
        asyncio.run(run_sequence(store, broken))
        rows = [list(r) for r in store.rows()]
        if broken:
            assert len(rows) == 3 and rows[0] == expected_rows()[0]
        else:
            assert rows == want
