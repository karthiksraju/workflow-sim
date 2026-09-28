# Authoring an adapter

Start with a workflow that has a visible effect: a persisted row, an emitted
message, or a durable completion marker. Call the application's real entrypoint.
Replace the network/storage/provider boundary, not the decision function whose
correctness you are testing.

1. Seed realistic initial state: old derived data, prior attempts and completion
   markers. A replacement test starting from an empty database misses stale data.
2. Model only the external contract needed by the scenario. State its provenance:
   documented schema, producer implementation, or sanitized captured response.
3. Schedule failures and changes at explicit offsets. Assert the stored payload,
   final markers and absence of duplicate effects. A call count alone is rarely enough.
4. Run a negative control. Break the intended property and show the same assertions
   reject it. Keep both the case and the evidence of why it fails.
5. Repeat with the same seed, then vary relevant ordering, delays and failure points.
   A larger seed count without new behaviors does not necessarily improve coverage.

The shipped retry fixture models its own synthetic receiver: an atomic
idempotency-key write followed by an acknowledgement that may be lost. It makes no
claim about a vendor's HTTP contract. The Celery example goes through the real
producer/task tracer and verifies JSON payload and retry count.

Do not use live production credentials. Adapters are trusted Python and may read
files and environment variables. The runner blocks common socket and process APIs
as a mistake detector; it cannot safely execute untrusted user uploads.

## Existing applications and advanced bindings

The experimental `Engine` accepts `clock_seam`, `asyncio_bridge`, and `log_module`.
They are explicit application integration points:

- Clock seam: `install(provider)` / `uninstall()`; provider has now/time/monotonic/sleep.
- Async bridge: `run_coro_sync(coro)` and `_ensure_background_loop()`;
  an existing loop may be exposed as `_LOOP`. Imported `run_coro_sync` aliases are
  rebound while the engine is installed, then restored.
- Structured logger: `log_event(event_id, msg='', level='info', data=None,
  exc_info=False)`. The ledger captures aliases and restores them at uninstall;
  captured stale wrappers delegate to the original logger after uninstall.

These bindings preserve the first consumer's behavior without importing its
application inside this library. They are experimental because one application's
bridge/log conventions are not yet a universal adapter protocol. Keep them in
application-owned compatibility modules; do not add application imports here.

The meeting consumer owns provider models, YAML scenarios, production entrypoints,
contract captures and business assertions. They do not belong in the shared package.
Every library upgrade must preserve its healthy regressions **and** declared
counterexamples. Do not turn a known failing product case into a green test by
weakening assertions during extraction.

Request new runtime features with two concrete workflows, the missing behavior,
and an observable failing case. This is the threshold for a new shared abstraction,
not a reason to add a plugin registry or workflow-specific helper preemptively.
