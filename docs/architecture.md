# Architecture

The library owns **when work runs and what evidence it produces**. The adapter
owns **what the application does, how external systems respond, and what correct
behavior means**. Both execute in a disposable worker process.

## Where each responsibility lives

```mermaid
flowchart TB
    User["Test / CLI"] --> Runner["Parent supervisor<br/>input + budgets"]
    Runner --> Worker
    subgraph Worker["Fresh worker process for one run"]
        direction TB
        Adapter["Adapter<br/>state + failures"]
        App["Real application code"]
        Boundaries["Boundary models"]
        Assertions["Final assertions"]
        Kernel["Shared runtime<br/>time + ownership"]
        Ledger["Evidence"]
        Adapter --> App
        App <--> Boundaries
        Adapter --> Assertions
        Kernel --> App
        App --> Ledger
        Boundaries --> Assertions
        Assertions --> Ledger
    end
    Worker --> Reap["Reap process group"]
    Reap --> Verify["Verify identity + verdict"]
    Verify --> Result["Return result"]
```

The parent never installs clock or Celery patches. The worker does. Normal runs,
failed runs and interrupted runs all go through parent-owned process cleanup.
This separates simulator state between tests; it does not restrict a trusted
adapter's filesystem access or turn Python into a security sandbox.

## One run, from request to evidence

```mermaid
sequenceDiagram
    participant T as Test / CLI
    participant P as Parent runner
    participant W as Worker process
    participant A as Adapter + application
    T->>P: adapter, inputs, seed, duration, budgets
    P->>P: Validate finite JSON and limits
    P->>W: Start process with fresh attempt ID and scratch directory
    Note over P,W: Parent wall deadline includes setup, execution and teardown
    W->>W: Install boundary guards and runtime hooks
    W->>A: build(context)
    A->>W: Schedule callbacks and register assertions
    W->>A: Execute events, tasks and timer wakeups
    A-->>W: Observable state + causal events
    W->>A: Read final assertion values
    W->>W: Teardown runtime and write result atomically
    W-->>P: Evidence, provenance and outcome
    P->>W: Reap owned process group
    P->>P: Check attempt, request hash, library hash and verdict
    P-->>T: PASS or explicit non-PASS outcome
```

If a worker times out, exits abnormally or publishes invalid evidence, the parent
returns a non-PASS result. A worker's claim of PASS is insufficient on its own.

## Inside the scheduler

```mermaid
flowchart LR
    Items["Timeline callbacks"] --> Select["Select next due work" ]
    Tasks["Celery tasks"] --> Select
    Timers["Asyncio timers"] --> Select
    Select --> Execute["Owned execution<br/>threads + loop + pool"]
    Execute --> State{"Anything runnable?"}
    State -- Yes --> Execute
    State -- "All parked" --> Advance["Advance time<br/>within horizon"]
    Advance --> Select
    Execute --> Report["Execution report"]
```

At equal instants, timeline items precede queued Celery work, which precedes timer
wakeups. Items retain insertion order; equal-deadline loops wake in park order.
The scheduler waits for owned thread-pool work before advancing time. Hard crash
fencing and graceful cancellation are distinct behaviors, covered by separate tests.

This is one controlled scheduling model. It does not explore every possible
interleaving or reproduce a real broker's distribution and acknowledgement behavior.

## Completion belongs to the whole execution

```mermaid
flowchart TD
    Body["Entry coroutine returns"] --> Children{"Children, timers or<br/>pool work still active?"}
    Children -- Yes --> Owned["Keep execution owned<br/>advance or report INCOMPLETE"]
    Owned --> Children
    Children -- No --> Errors{"Unhandled callback or<br/>unretrieved task error?"}
    Errors -- Yes --> Failed["Record execution failure<br/>HARNESS_ERROR"]
    Errors -- No --> Done["Execution complete<br/>evaluate business assertions"]
    Crash["Injected hard crash"] --> Fence["Fence live owned threads<br/>including descendants"]
    Owned --> Crash
```

The runtime retains created tasks/futures through evidence collection so garbage
collection cannot hide an unobserved exception. CPython 3.12's exception-retrieval
flag distinguishes handled errors from unhandled ones; application loop handlers
still run. Final health is refreshed after assertions. Celery retries preserve
continuation metadata at signature production, while the simulator owns exactly
one continuation dispatch. Each delivery decodes the saved serialized message.

[Alpha 2 corrections](alpha2.md) map the adversarial findings to their regression
checks and describe changed evidence semantics.

## Extraction and the first consumer

```mermaid
flowchart LR
    Library["Personal repository<br/>workflow-sim source + conformance suite"] --> Wheel["Built wheel<br/>version + SHA256"]
    Wheel --> Pin["Meeting consumer<br/>pinned immutable artifact"]
    Pin --> Bindings["Small compatibility modules<br/>clock seam · async bridge · logger"]
    Bindings --> Meeting["Application-owned simulator<br/>provider models · scenarios · business checks"]
    Pin --> Identity["Harness manifest hashes the wheel<br/>and copies it into historical replays"]
    Meeting --> Compare["Compare outcomes and assertion identities<br/>with pre-extraction evidence"]
```

The wheel is a generated dependency artifact, not a second editable implementation.
Private-alpha vendoring avoids distributing personal GitHub credentials to the
application's CI and keeps historical proof runs bound to their executed bytes.
A loader refuses a modified wheel or an already-imported package from another source.

The meeting adapter stays in its application repository. The library includes six domain reference workflows and two
small introductory examples, so a new workflow can use it without meeting code,
customer fixtures, application configuration or test dependencies.

## Code map and extension points

| Concern | Source | Change it when… |
| --- | --- | --- |
| Process lifecycle and limits | [runner.py](../src/workflow_sim/runner.py), [_worker.py](../src/workflow_sim/_worker.py) | Ownership or cleanup behavior changes. |
| Adapter authoring | [context.py](../src/workflow_sim/context.py) | Two real consumers need a shared operation. |
| Verdict and result validation | [contracts.py](../src/workflow_sim/contracts.py) | Evidence rules change; consider a schema version. |
| Virtual time and execution | [clock.py](../src/workflow_sim/clock.py), [engine.py](../src/workflow_sim/engine.py) | An independent timing/ownership probe fails. |
| Celery semantics | [celery_driver.py](../src/workflow_sim/celery_driver.py) | A supported producer/task behavior is missing. |
| Evidence and identity | [ledger.py](../src/workflow_sim/ledger.py), [provenance.py](../src/workflow_sim/provenance.py) | Records omit or misattribute an observable fact. |
| Application boundaries | Your adapter repository | A provider/storage contract or workflow changes. |

Stable-for-this-alpha entrypoints are `run()` and the documented context methods.
The advanced engine and application bindings remain experimental. A new backend,
plugin registry or generalized storage model needs concrete consumers before it
belongs here. See [the boundary decision](adr/0001-alpha-boundary.md) and
[the exact API contract](contracts.md).

## How changes earn release confidence

The [validation architecture](confidence.md) combines independent arithmetic and
generated lifecycle models, ordinary asyncio, actual Redis/prefork Celery workers,
and targeted mutations. Both platform jobs and the confidence job gate release
promotion. Keep these tools in development dependencies; consumers only install
the execution runtime and its bounded runtime dependencies.
