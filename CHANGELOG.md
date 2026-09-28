# Changelog

## 0.1.0a2 — adversarial review corrections

- Keep asynchronous descendants in flight after their entry coroutine returns;
  fence only live thread identities when a descendant is crashed. Async bridges
  inherit pool ownership and publish work atomically with blocking the caller.
- Include unhandled callback and unretrieved task/future errors in execution health,
  including retained tasks and errors created while evaluating assertions.
- Preserve Celery retry continuations, signature options and original message
  contents across duplicate/crash deliveries. Record completion-hook/publication
  failures and caught unsupported operations; disarm completed task limits.
- Reject unowned threads and pool submissions throughout the public worker lifecycle.
- Invalidate explicit CLI output on argument-parsing errors and return exit 4.
- Include the contributor dependency lock in source archives and verify its bytes.

Result schema remains 1. Previously misleading PASS/INCOMPLETE results can change;
rerun evidence rather than reclassify archived results. See [migration and regression
coverage](docs/alpha2.md). Version 0.1.0a1 is retained for historical reproduction.

## 0.1.0a1 — internal alpha

- Extracted the virtual clock, scheduler, Celery driver, execution fencing and
  causal ledger behind explicit application bindings.
- Added a process-per-run API and CLI, JSON assertions, bounded wall time/output,
  best-effort boundary guards, result validation and content provenance.
- Included async retry/deduplication and Celery retry examples with negative controls.
- Carried over clock, scheduler and thread-pool conformance tests.
- Added wheel/source distribution checks, Linux/macOS CI and a draft-release workflow.

Supports CPython 3.12 on Linux/macOS. Result schema 1; advanced kernel APIs are
experimental. Private distribution only while ownership/licensing is settled.
