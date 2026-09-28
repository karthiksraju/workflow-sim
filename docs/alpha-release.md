Internal alpha 2 for CPython 3.12 on Linux and macOS. This corrects all eleven
verified findings from the independent review of alpha 1.

- Account for asynchronous descendants, unobserved errors and thread ownership.
- Preserve Celery retry continuations, signature options and original delivery
  payloads; report publication failures and unsupported operations.
- Disarm completed task deadlines, invalidate stale CLI output on parse errors,
  and include the contributor lock in the source distribution.
- Keep async bridge ownership and coroutine handoff atomic with the scheduler.

Validation: 316 installed-package tests pass on Linux and macOS; all 19 original
reviewer witnesses/controls pass their corrected expectations. Old-code negative
controls demonstrate the regressions. See [validation](https://github.com/karthiksraju/workflow-sim/blob/main/docs/validation.md)
for consumer results, exact revisions and remaining model limits.

Upgrade to `v0.1.0a2` and rerun scenarios. Do not reinterpret archived alpha 1
verdicts: they lack the corrected execution evidence. The public API and result
schema 1 remain unchanged; verdicts, deadline events and frozen task IDs may change.
See [migration](https://github.com/karthiksraju/workflow-sim/blob/main/docs/alpha2.md).

This is a trusted-adapter testing runtime. PASS establishes the supplied assertions
in that model, not live provider/broker/database correctness. The same nine known
application failures remain visible in the meeting consumer.

Private distribution only; public licensing/visibility remains pending. These
assets are promoted from the exact release commit's CI, without rebuilding.
From the downloaded asset directory, run `sha256sum -c SHA256SUMS`
(or `shasum -a 256 -c SHA256SUMS` on macOS).
