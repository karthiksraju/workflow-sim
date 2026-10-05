# Storage boundary contract: SQLite idempotency store

One grounded storage boundary for the live-cert program: a file-backed SQLite
table with `UNIQUE(event_id)` where each delivery is a single `INSERT OR
IGNORE`, which SQLite documents as atomic.

## Provenance

- Engine: CPython 3.12 stdlib `sqlite3`, SQLite 3.53.1 in the tested
  environment (recorded per run via `sqlite3.sqlite_version`).
- Semantics: SQLite `UNIQUE` constraint plus `INSERT OR IGNORE` conflict
  handling, per the SQLite `ON CONFLICT` documentation. Values synthetic.
- Conformance: `tests/test_storage_contract.py` over
  `tests/adapters/storage_conformance.py`. The same store code and delivery
  sequence (seeded older row, commit-then-lost-acknowledgement retry,
  duplicate) run under workflow-sim virtual time and under real execution;
  both assert the exact receipt rows.

## What the negative control catches

Deriving the idempotency key per attempt (`event:attempt`) inserts one row per
attempt. The `exact receipt rows` check asserts the correct two rows
unconditionally, so the broken variant fails with three rows in both
executions. The plausible bug: unstable key derivation duplicating receipts on
retry.

## Boundary lessons applied

- State must be JSON-shaped: `store.rows()` returns tuples, which the context
  rejects (`json_value`); the assertion reads `[list(r) for r in ...]`.
- Thread affinity: the runtime executes setup, callbacks and assertion reads
  sequentially on different owned threads, so the store shares one connection
  with `check_same_thread=False`. No concurrent writers are claimed.

## Limits

Single-process file database; no WAL/concurrency, server-database, or
provider claims. One controlled schedule; seed and timing bounds from
[contracts](contracts.md) still apply.
