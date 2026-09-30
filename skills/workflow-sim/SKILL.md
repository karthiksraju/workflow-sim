---
name: workflow-sim
description: Set up workflow-sim in an existing Python application, reproduce async or Celery workflow failures with controlled time and IO, and validate fixes with exact-state assertions and negative controls. Use for retries, duplicate effects, partial writes, stale state, or unfinished async work. Live infrastructure load tests and model-output quality need separate tools.
---

# Use workflow-sim on a real workflow

Produce a small adapter that calls the application's real code, reproduces an
observable failure, and checks the fix under the same modeled conditions.

## Establish the environment

1. Locate the application entrypoint and its existing tests. Identify the queue,
   Python version, dependency tool, and external effects before choosing a case.
2. Locate a workflow-sim checkout matching the installed or selected revision.
   If absent, clone `https://github.com/karthiksraju/workflow-sim.git` into a separate
   directory and select the intended tag or commit. Public HTTPS access needs no
   GitHub credentials.
   For new users, start with `v0.1.0a5`, the first tag with this skill, uv setup
   and MIT metadata. Earlier tags lack the skill and contain private-era setup
   text: keep this skill's checkout separate and use public HTTPS when inspecting
   one. Read the selected revision's `README.md` and `pyproject.toml`.
   Support on a newer branch does not change an older tag's Python requirements.
3. Use the application's compatible environment, or an isolated uv project with
   its required application dependencies. Pin workflow-sim to an immutable commit
   or release tag and retain the lockfile. Preserve the application's Python range
   and dependency tooling; report incompatibility rather than forcing a downgrade.
   In an existing uv app, use `uv add --dev`; name the dependency/lock files
   changed in your handoff. Dev groups still share version resolution, so use
   a separate environment if the app's constraints conflict. For a new trial,
   follow the checkout's uv installation recipe.
4. Read the matching checkout's `docs/contracts.md` for supported execution paths
   and `docs/adapters.md` for boundary modeling. Celery is the queue backend;
   direct async functions can run without testing broker delivery semantics.
   For an existing Celery app, follow the Celery section of `docs/adapters.md`
   and the `celery_retry` example; call its real tasks through `.delay` or
   `.apply_async` and retain required broker/backend client dependencies.

Run trusted application code in a disposable environment without production
credentials. The worker inherits environment variables and can access files;
process isolation is not a security sandbox. Keep disposable adapters and output
local unless the user asks to add them to the application repository.

## Verify installation before adapting the application

From the uv trial/application project, run the shipped smoke example:

```sh
uv run --locked workflow-sim workflow_sim.examples.retry:build --duration 10 --output smoke-fixed.json
printf '{"broken": true}\n' > smoke-broken-input.json
uv run --locked workflow-sim workflow_sim.examples.retry:build --inputs smoke-broken-input.json --duration 10 --output smoke-broken.json
```

The first result must be `PASS`. The second must be `ASSERTION_FAILED` with exit
code 1 and a duplicate-delivery assertion showing the extra payload. Read both
JSON files. This verifies setup, not the application's behavior. For a non-uv
application environment, invoke its installed `workflow-sim` executable directly.

## Build the application adapter

- Choose one failure tied to the issue. Trace the real producer, handler, retries,
  persistence and completion markers. Read only the closest example in
  `docs/examples.md` and `src/workflow_sim/examples/` in the matching checkout.
- Write the expected final content before implementing the fake. Seed prior failed
  attempts, existing completed data and an unrelated record where relevant.
- Import the real application entrypoint. Replace network/storage/provider
  boundaries where the application looks them up; retain the decision logic being
  tested. Name each fixture's source: producer code, documented schema or sanitized
  capture. A synthetic contract remains an assumption to verify separately.
- Define `build(ctx)` in an importable module. Schedule entrypoint calls with
  `ctx.at(seconds, label, callback)` and register exact JSON state/content checks
  with `ctx.expect(name, actual_callable, expected)`. Include preserved unrelated
  state, required effects and missing duplicates. Call counts alone cannot prove
  payload correctness. The factory configures the run and returns `None`.
- Set the horizon to cover the retries and follow-up work being claimed. Use
  `--start-at` with an explicit timezone for date-sensitive code; the default
  origin is in 2099. Put asynchronous work inside scheduled executions. Use
  `asyncio.to_thread` or `run_in_executor` for supported pool work; unowned setup
  threads and direct pool submission are rejected by the public worker.

Run through the public CLI or `workflow_sim.run`, which creates a fresh worker.
For example, from the directory containing `my_adapter.py`:

```sh
uv run --locked workflow-sim my_adapter:build --duration 180 --output application-result.json
```

Adjust module and duration to the scenario. For adapters elsewhere, pass
`--project-dir` to the import root. Install application dependencies into the
invoking environment: the child ignores caller `PYTHONPATH` and uses a temporary
working directory. Resolve fixture paths through `ctx.project_dir`, not cwd.

## Validate the fix and report the boundary

Run the original code and the fix with identical inputs, modeled failures and
assertions. The original must fail the intended business assertion and the fix
must pass it with clean execution health. A timeout or import error is not a
negative control. If no defect is present, introduce a plausible isolated mutation
and report it as a synthetic control, not a discovered production bug.

Inspect `outcome`, `error`, and—when present—`evidence.checks`, `evidence.report`,
`evidence.unsupported` and `evidence.violations`. Diagnose `INCOMPLETE`,
`UNSUPPORTED` and `HARNESS_ERROR`; none validates the fix. Preserve checks when
addressing these outcomes. Null evidence means the worker did not produce a
verified result; inspect the supervisor error first.

Repeat with the same code, dependencies, fixtures, inputs, seed and horizon;
compare `evidence_sha256`. Investigate differences before calling the case
reproducible (for example, an adapter may record real elapsed time). Then vary
relevant failure points or ordering. Use stock asyncio or an independent reference where practical. If two
models disagree, resolve the boundary assumption before claiming confidence.
More seeds do not establish more business or live-system coverage by themselves.

Report the application/library revisions, Python/dependencies, commands, inputs,
seed, horizon, modeled contracts, original/fixed outcomes and exact assertions.
Retain sanitized evidence and identify unverified external contracts. A `PASS`
means those assertions held in that modeled run; it does not certify the live
broker, provider, arbitrary thread interleavings or AI answer quality.
