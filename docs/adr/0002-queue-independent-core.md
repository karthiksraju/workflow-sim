# 0002 — Queue-independent core with explicit execution adapters

Status: **Proposed**. Not accepted, and nothing here is implemented.

Amends [ADR 0001](0001-alpha-boundary.md) only if accepted. It replaces the
sentence "Celery remains a declared alpha dependency: separating a second queue
backend is deferred until a concrete consumer needs it." Every other statement
in ADR 0001 is unchanged: no generic plugin discovery, the process-per-run model,
the CPython 3.12 and POSIX support claims, and trusted scenario adapters.

Full design: [queue-independent core specification](../design/queue-independent-core.md).
Delivery order: [implementation plan](../design/queue-independent-implementation-plan.md).

## Context

At `57fa0fe` (the baseline after `v0.1.0a3`), the engine imports and constructs
the Celery driver on every run. Every run therefore loads Celery, Kombu and
Billiard and patches `Task.apply_async`, including runs that are pure asyncio.
The parent process reads Celery's package metadata for provenance, so a
core-only install fails with `PackageNotFoundError` before any worker starts.

Three probes made the design constraints concrete:

- Removing the interception leaves `memory://` publishes silently lost.
- `app.send_task` already bypasses the virtual queue and yields a false `PASS`.
- The simulated redelivery-after-crash happens on request. It does not depend on
  `task_acks_late` or `task_reject_on_worker_lost`, unlike a real worker.

Most target workflows (billing, fulfillment, ingestion, documents, monitoring)
are asyncio, thread and pool code, and need none of Celery.

## Decision

1. **The core owns everything except framework delivery.** That covers virtual
   time, scheduling and equal-time order, execution lifecycle and descendants,
   crash fencing, limit enforcement, failure injection, the sticky unsupported
   sink, assertions, evidence and the verdict. No core module imports `celery`,
   `kombu` or `billiard`, at top level or lazily. A static gate and a runtime
   gate enforce this.
2. **One private seam, `DeliveryAdapter`.** It exposes peek, pop and pending over
   time-ordered deliveries; begin, execute and settle for each delivery; the
   limit values and the soft exception; report and ledger projections; and
   install and uninstall. Crash consequences reach the adapter as a
   `Crashed(...)` settlement, and **the adapter decides the message's fate**. The
   seam dispatches framework work. It is not a broker abstraction.
3. **Two in-tree executions, chosen from a closed table.**
   - `asyncio` (contract `asyncio/1`): no deliveries.
   - `celery` (contract `celery-virtual/1`): today's driver with unchanged
     behaviour, kept at `workflow_sim/celery_driver.py`.

   There are no entry points and no registry. Adding a row needs an ADR and
   differential evidence against a real worker.
4. **Selection is explicit.** It comes from `run(execution=...)`,
   `--execution` or `Engine(execution=...)`. It is recorded in the request
   (schema 2) and bound into the request hash. Nothing selects an execution
   automatically.

   A **framework sentinel** runs at the end of the run. It checks
   `sys.modules`, and if a known task framework was imported without being
   selected, it makes the run `UNSUPPORTED`. The sentinel only rejects runs; it
   never selects an execution.
5. **Identity is recorded and checked by the parent.** `provenance.execution`
   records the name, contract id, dependency versions, options, capabilities
   (`unsupported`, `not_configured` or `modeled`) and assumption ids. The parent
   recomputes the expected block without importing the framework, and rejects
   any mismatch. Runtime code never claims *validated*. Validation lives only in
   the support-claims matrix, which cites `docs/validation` records.
6. **Staged rollout.**
   - **U0** fixes the `send_task` false `PASS` independently.
   - **Phase 1 (`0.1.0a4`)** adds the seam, keeps Celery as a hard dependency
     with `"celery"` as the default, and adds a core-only wheel gate.
   - **Phase 2** makes Celery redelivery derive from ack configuration
     (`celery-virtual/2`), if the product accepts it.
   - **Phase 3 (`0.2.0a1`)** moves Celery to the `[celery]` extra and changes
     the default to `"asyncio"`.

## Consequences

**Benefits**

- Asyncio-only users can install and run without Celery.
- Evidence states which delivery model produced a verdict, and under which
  assumptions.
- A future framework has one seam to satisfy, without inheriting Celery's
  internals.

**Costs and risks**

- Result and request `schema_version` changes from 1 to 2.
- The consumer re-pins its kernel file hashes.
- Moving the adapter install stage could perturb the RNG order. The
  evidence-digest identity gate guards against this.
- The sentinel rejects runs that import Celery only for type hints; such runs
  must select `celery`.

**Not claimed**

- Support for RabbitMQ, SQS, Redis visibility timeouts, or any second framework.
- Direct-queue consumers are modelled as boundary fakes on core time, not as
  execution adapters.
- Existing real-worker evidence still covers only Celery 5.6.3, Redis and the
  prefork pool on Linux, with `acks_late` and `reject_on_worker_lost` enabled.

## Alternatives rejected

- The status quo.
- Lazy import alone, with no seam.
- A plugin registry or entry points.
- A universal broker abstraction.
- Detecting the framework automatically from imports.
- Declaring the selection inside the scenario module.
- Modelling a direct SQS consumer as an execution adapter.

Moving the driver into a subpackage or a separate distribution is deferred.
The spec, §19, gives the reasoning for each.

## Decisions needed before acceptance

- **D1**: the phase 3 default (recommended: `"asyncio"`). This blocks phase 3
  only.
- **D3**: whether Celery crash redelivery follows `acks_late` and
  `reject_on_worker_lost` (recommended: yes). This changes some current `PASS`
  results for scenarios that request redelivery under the default
  configuration. It blocks phase 2 only.

Phase 1 needs neither decision and can be accepted on its own.
