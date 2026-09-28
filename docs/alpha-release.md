Internal alpha for CPython 3.12 on Linux and macOS.

The package provides a deterministic virtual-time kernel, Celery execution model,
process-per-run API, JSON assertion evidence and two runnable workflow examples.
See README for installation and deliberate duplicate-delivery failure.

This is a testing runtime for trusted adapters. It is not a security sandbox or a
certification of live system behavior. Adapter/provider contracts and assertions
determine what each PASS establishes. Advanced kernel APIs may change during alpha.

Private distribution only; public licensing/visibility remains pending. See
`docs/validation.md` for exact verification and outstanding limitations.

Validation: 267 installed-package tests pass on Linux and macOS. The meeting
consumer comparison preserves all 102 outcomes, assertions and application-state
snapshots; its nine unresolved product failures remain visible. Kernel mutation
and process-boundary negative controls are included. See the validation report
for the original consumer-suite failures and their successful targeted reruns.

Architecture, execution lifecycle, verdict rules and release promotion are
illustrated in the docs. These assets are the tested CI distributions; SHA256SUMS
also covers the two platform test reports. From the downloaded asset directory,
run `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c SHA256SUMS` on macOS).
