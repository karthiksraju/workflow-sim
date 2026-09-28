# Alpha delivery

- [x] Extract runtime without application imports; retain source provenance.
- [x] Isolated public runner, strict input/result contract and useful CLI.
- [x] Independent workflow examples with user-visible assertions and negative controls.
- [x] Inherited conformance and crash-containment probes.
- [ ] Meeting adapter integration and before/after extraction comparison.
- [ ] Installed wheel/sdist checks in a clean environment.
- [ ] Linux/macOS CI, release artifacts and compatibility/dependency policy.
- [ ] Contribution, issue intake, security, ownership and release documentation.
- [ ] Record exact validation, unsupported behavior and release status.

Public visibility and licensing await the owner's decision. Do not publish a
package or make the repository public by assuming a license.


Initial extraction validation: 260 tests passed on CPython 3.12.13/macOS in
37.21s, including inherited scheduler/clock/pool probes and real-process API
negative controls. Direct kernel tests expose the inherited interpreter-shutdown
monitoring callback warning; the public worker uses process exit. Investigate
that warning before closing the alpha checklist.
