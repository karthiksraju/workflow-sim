# Changelog

## Unreleased

- Publish the repository under MIT; add license metadata and packaged license text.
  Use public HTTPS setup links and GitHub private vulnerability reporting.

- Add a portable workflow-sim agent skill for setup, real application adapters,
  negative controls and evidence reporting. Include it in the source distribution.

- Use uv for contributor setup, Python selection, CI and package/mutation checks.
  `uv.lock` replaces `requirements-dev.lock`; development tools move from the
  `dev` package extra to the `dev` dependency group. Dependency versions are preserved.
  Existing alpha tags and runtime behavior are unchanged.

## 0.1.0a4 — publication guards and calendar origins

- Reject unmodeled Celery `send_task` and direct Kombu/AMQP publication, even
  when the application catches the exception. Previously, a weak business
  assertion could PASS while published work never executed.
- Add timezone-aware `start_at` / `--start-at`, normalized to UTC and bound to
  verified request/provenance. Preserve the default 2099 origin and request shape.
- Keep result schema 1, the existing queue model and supported runtime matrix.

See [migration and verification scope](docs/alpha4.md). Alpha 3 stays immutable.

## 0.1.0a3 — domain examples and independent confidence gates

- Add billing, fulfillment, ingestion, document/AI processing, monitoring and
  meeting workflows with broken controls and illustrated boundary documentation.
- Compare generated asyncio workloads with stock execution and independent models.
- Compare four Celery contracts with real Linux prefork workers and isolated Redis,
  including actual worker-loss redelivery and original-message preservation.
- Require six targeted runtime mutations to fail behavioral assertions.
- Gate artifact promotion on these checks and ship their evidence with the release.
- Add tester onboarding, confidence recipes and a factual launch-post draft.

Runtime modules and result schema are unchanged from alpha 2 (apart from the
package version). No new execution features or platform support are claimed.

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
