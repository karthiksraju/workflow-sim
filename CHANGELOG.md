# Changelog

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
