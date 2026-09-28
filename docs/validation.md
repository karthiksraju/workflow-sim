# Alpha validation

Validation is in progress. Initial extraction: 260 tests passed on CPython 3.12.13
macOS. After process/result hardening and fixing interpreter-shutdown monitoring
callbacks: 263 passed in 38.17 seconds, without shutdown warnings.

The suite covers independent timer arithmetic, queue-model ordering, JSON task
serialization, retries, native asyncio semantics, pool ownership and hard versus
graceful abandonment. Public API tests use actual processes and inspect payloads,
assertion witnesses, ledger contents, repeat hashes and cleanup outcomes. A deliberate
deduplication bug produces duplicate content and is rejected by the same assertions.

Remaining validation work: installed artifacts, both platform CI, and meeting
consumer upgrade comparison. Exact final evidence will replace this progress note.

Unverified external contracts include live Celery brokers, databases, provider
APIs and production captures. The inherited meeting simulator's known product
counterexamples are not library bugs fixed by this extraction. The library does
not expand provider contract coverage merely by becoming installable.

First hosted CI exposed the inherited 500 ms total watchdog on a 10,000-iteration
zero-delay stress test (macOS runner: 262 pass, one timeout). That is an accidental
throughput threshold, not the timer contract. Only this stress case now has a
5-second wall bound; the exact no-time-advance/yield-order checks remain. Added
negative controls that shift normalized timers by 1 microsecond (must fail those
assertions) and stall a coroutine (must hit the unchanged default watchdog).
