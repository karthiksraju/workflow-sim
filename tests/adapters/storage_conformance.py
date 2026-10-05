"""SQLite-backed idempotency store: duplicate delivery writes exactly one row.

Boundary contract: CPython 3.12 stdlib sqlite3 (version recorded in the
store's meta table per run; 3.53.1 in the tested environment), file-backed,
one table with UNIQUE(event_id); each delivery is a single INSERT OR IGNORE,
which SQLite documents as atomic. Values synthetic. The broken variant
derives a per-attempt key, so a retried delivery inserts a second row.
"""
import asyncio
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(
  event_id TEXT PRIMARY KEY,
  invoice TEXT NOT NULL,
  customer TEXT NOT NULL,
  amount INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
"""

ROW_QUERY = ('SELECT event_id, invoice, customer, amount FROM receipts'
             ' ORDER BY rowid')


def fresh_rows(path):
    """Read committed rows through a NEW connection: uncommitted writes on the
    writing connection stay invisible, so this catches a false PASS from
    buffered-but-uncommitted state."""
    db = sqlite3.connect(path)
    try:
        return [list(r) for r in db.execute(ROW_QUERY).fetchall()]
    finally:
        db.close()


def fresh_meta(path):
    db = sqlite3.connect(path)
    try:
        return dict(db.execute('SELECT key, value FROM meta').fetchall())
    finally:
        db.close()


class SqliteStore:
    def __init__(self, path):
        # The runtime executes setup, callbacks and assertion reads
        # sequentially on different owned threads, so one connection is shared
        # with check_same_thread=False. No concurrent writers are claimed.
        # autocommit (isolation_level=None) commits every write; assertions
        # still read through fresh connections to prove committed state.
        self.db = sqlite3.connect(path, isolation_level=None,
                                  check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.execute('INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)',
                        ('sqlite_version', sqlite3.sqlite_version))

    def store(self, key, invoice, customer, amount):
        self.db.execute(
            'INSERT OR IGNORE INTO receipts(event_id, invoice, customer, amount)'
            ' VALUES(?, ?, ?, ?)', (key, invoice, customer, amount))


async def deliver(store, event_id, invoice, *, broken=False, attempt=0):
    key = f'{event_id}:{attempt}' if broken else event_id
    store.store(key, invoice, 'cus-7', 1200)
    if attempt == 0:
        raise TimeoutError('receipt committed; acknowledgement lost')
    await asyncio.sleep(0)


async def run_sequence(store, broken):
    try:
        await deliver(store, 'in-42', 'in-42', broken=broken, attempt=0)
    except TimeoutError:
        await asyncio.sleep(2)
        await deliver(store, 'in-42', 'in-42', broken=broken, attempt=1)
    await deliver(store, 'in-42', 'in-42', broken=broken, attempt=1)


def build(ctx):
    db_path = ctx.inputs['db']
    store = SqliteStore(db_path)
    store.store('evt-old-9', 'in-9', 'cus-7', 300)  # realistic existing state
    broken = ctx.inputs.get('broken', False)

    async def sequence():
        await run_sequence(store, broken)

    ctx.at(0, 'deliver-with-retry-and-duplicate', sequence)
    # The correct expectation holds for both variants: the broken key
    # derivation must fail this check, not match a broken expectation.
    ctx.expect('exact receipt rows', lambda: fresh_rows(db_path),
               [['evt-old-9', 'in-9', 'cus-7', 300],
                ['in-42', 'in-42', 'cus-7', 1200]])
    ctx.expect('sqlite version recorded', lambda: fresh_meta(db_path),
               {'sqlite_version': sqlite3.sqlite_version})
