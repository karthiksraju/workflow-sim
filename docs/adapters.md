# Authoring an adapter

Choose an observable effect: a stored row, delivered message or completion marker.
Call the application's real entrypoint and model its external services and storage.
Keep the decision logic under test running.

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

The retry example models an atomic idempotency-key write followed by a lost
acknowledgement. This is a synthetic receiver contract. The Celery example uses
the real producer/task tracer to check JSON payloads and retry counts.

Use a disposable environment without production credentials. Adapters can read
files and environment variables; see [security](../SECURITY.md).

## Why the retry example catches a real failure shape

```mermaid
sequenceDiagram
    participant App as Real retry loop
    participant Receiver as Receiver model
    participant Check as Final assertions
    App->>Receiver: Deliver invoice-42, amount 1200
    Receiver->>Receiver: Commit payload and idempotency key
    Receiver--xApp: Acknowledgement lost
    Note over App: 5s virtual delay
    App->>Receiver: Retry invoice-42
    alt Deduplication works
        Receiver->>Receiver: Keep the original delivery
        Receiver-->>Check: One exact payload
    else Deliberate bug enabled
        Receiver->>Receiver: Append a duplicate delivery
        Receiver-->>Check: Two payloads
    end
    Check->>Check: Require one exact delivery
```

The failure starts after the first durable effect. Testing only a timeout before
any write would not expose this duplicate-delivery bug. The assertion inspects
persisted content, not just whether retry code was called.

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

These experimental bindings preserve the first consumer's bridge/log conventions.
Keep the compatibility modules in the application; the library must not import it.

The meeting consumer owns provider models, YAML scenarios, production entrypoints,
contract captures and business assertions. They do not belong in the shared package.
Every library upgrade must preserve its healthy regressions **and** declared
counterexamples. Do not turn a known failing product case into a green test by
weakening assertions during extraction.

Propose a shared abstraction with two concrete workflows and a failing case that
the current boundary cannot express. Keep workflow-specific helpers in the adapter.
