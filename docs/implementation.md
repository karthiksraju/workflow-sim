# Alpha delivery

- [x] Extract runtime without application imports; retain source provenance.
- [x] Isolated public runner, strict input/result contract and useful CLI.
- [x] Independent workflow examples with user-visible assertions and negative controls.
- [x] Inherited conformance and crash-containment probes.
- [x] Meeting adapter integration and before/after extraction comparison.
- [x] Installed wheel/sdist checks in a clean environment.
- [x] Linux/macOS CI, release artifacts and compatibility/dependency policy.
- [x] Contribution, issue intake, security, ownership and release documentation.
- [x] Record exact validation, unsupported behavior and release status.

Public visibility and licensing await the owner's decision. Do not publish a
package or make the repository public by assuming a license.


The extracted runtime has now received an independent adversarial review and
corrections for all eleven reproduced defects. Its 316 library tests pass on both
Linux/macOS CI, including 49 additional regressions/controls. All 19 original
review probes pass their corrected expectations against the final runtime wheel.
Consumer checks include 976 passing tests and six strict xfails on the initial
correction, followed by 192 affected-module tests and a 102-scenario comparison on
the final bridge fix. No known product counterexample was hidden.
See [validation](validation.md) for consumer evidence and [architecture](architecture.md)
for the illustrated runtime/adapter boundary. Alpha 2 migration instructions are
in [the correction notes](alpha2.md); alpha 1 remains available for reproduction.
Public visibility and licensing remain owner decisions.

[Private alpha 2](https://github.com/karthiksraju/workflow-sim/releases/tag/v0.1.0a2)
is published. Its exact release artifacts and GitHub-tag installation are verified.
