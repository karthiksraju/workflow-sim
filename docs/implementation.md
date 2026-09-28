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


Runtime and packaging implementation is complete. Final evidence: 267 library
tests on both Linux/macOS CI, 102 unchanged meeting scenario results, nine caught
policy mutations, and passing targeted checks for the consumer issues found.
See [validation](validation.md) for exact results and [architecture](architecture.md)
for the illustrated runtime/adapter boundary. Version `v0.1.0a1` is available as a
private internal prerelease, and installation from its GitHub tag has been verified.
Public visibility and licensing remain owner decisions.
