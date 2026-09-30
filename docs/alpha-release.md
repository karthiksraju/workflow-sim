Alpha 4 for CPython 3.12 on Linux and macOS.

This release rejects Celery `send_task` and direct Kombu/AMQP publications that
bypass the modeled queue. The unsupported record persists when application code
catches the exception, preventing an unaccounted publication from producing PASS.
Registered task `apply_async`, `delay` and registered signatures retain their
supported behavior.

Calendar-sensitive workflows can set `start_at` / `--start-at` with an explicit
timezone. The origin is normalized to UTC and bound to verified configuration.
The default remains 2099-01-01 UTC. See [migration and scope](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a4/docs/alpha4.md).

Release gates cover installed packages on Linux/macOS, minimal installation,
six examples with broken controls, four real prefork/Redis comparisons and six
runtime mutations. The [verification report](https://github.com/karthiksraju/workflow-sim/blob/main/docs/validation/alpha4.md)
also records local stability, corpus and consumer checks, which are separate from CI.

Result schema remains 1 with an optional `start_at` configuration field. Existing
adapters that omit the option retain the old request shape. Rerun evidence for
previously unaccounted publication paths; do not reinterpret archived PASS files.

Six synthetic domain examples remain available. The independent corpus has 240
input timelines and 36 implemented reference business cases; 204 business cases
still need adapters. No additional queue backend or provider certification is
claimed. Queue-independent architecture work is deferred.

Draft release for review. The repository is public under the
[MIT license](https://github.com/karthiksraju/workflow-sim/blob/main/LICENSE). Assets
are promoted from the tested commit's CI, without rebuilding. Verify downloaded
assets with `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c SHA256SUMS` on macOS).
