# Alpha delivery

- [x] Extract runtime without application imports; retain source provenance.
- [x] Isolated public runner, strict input/result contract and useful CLI.
- [x] Independent workflow examples with user-visible assertions and negative controls.
- [x] Inherited conformance and crash-containment probes.
- [x] Meeting adapter integration and before/after extraction comparison.
- [x] Installed wheel/sdist checks in a clean environment.
- [x] Linux/macOS CI, release artifacts and compatibility/dependency policy.
- [x] Contribution, issue intake, security, ownership and release documentation.
- [ ] Record exact validation, unsupported behavior and release status.

Public visibility and licensing await the owner's decision. Do not publish a
package or make the repository public by assuming a license.


Current evidence: 266 library tests pass on both Linux/macOS CI; 102 meeting
comparison records preserve all assertions, state and execution health. The full
consumer self-test run and final release-artifact pin are still in progress. See
[validation](validation.md) for exact evidence and [architecture](architecture.md)
for the illustrated runtime/adapter boundary.
