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
