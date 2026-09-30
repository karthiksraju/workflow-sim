# Validation and remaining gaps

Alpha 4 has passed the checks below. Its [verification report](validation/alpha4.md)
records the tested artifacts, revisions, commands and qualifications. These results
support the modeled behavior; they do not certify arbitrary adapters or live systems.

| Evidence | Result | Limit |
| --- | --- | --- |
| Installed package | 325 tests on each of Linux and macOS | CPython 3.12 and the recorded dependencies |
| Stability acceptance | 41 checks on each platform; old alpha 3 fails 18 publication checks | Separate local harness, not permanent CI |
| Domain examples | Six corrected cases pass; six deliberate bugs fail | Synthetic workflows |
| Real workers | Four Celery/Redis comparisons match expected effects | Recorded transport, versions and worker configuration |
| Runtime mutations | Six existing and two stability-specific defects detected | Curated faults, not a whole-codebase mutation score |
| Independent corpus | 1,026 expected outcomes, including 36 business cases | 204 of 240 business cases still need adapters |
| Pinned consumer replay | All 102 outcomes preserved: 73 PASS, 29 known failures | Same application revision; application bugs remain |
| Consumer suite | 976 passing cases and six strict xfails across the full run and corrected four-test rerun | Not one clean full-suite invocation |

[Confidence recipes](confidence.md) explain how to reproduce the permanent gates.
Timeline execution, business assertions and live integration checks measure
different things; their counts are not interchangeable.

## Historical evidence

Earlier reports describe private releases as they were tested. The repository is
now public under the [MIT license](../LICENSE); historical results and tags remain
tied to their original revisions.

- [Alpha 1 extraction](validation-alpha1.md): original comparisons, before the
  independent review found runtime defects.
- [Alpha 2 corrections](validation/alpha2.md): adversarial regressions, consumer
  comparisons and published artifact identity.
- [Alpha 3 release index](validation/alpha3-release.json): examples, generated
  tests, worker comparisons, mutations and exact artifact hashes.
- [Alpha 3 corpus evaluation](validation/corpus-alpha3.md): independent timelines,
  the implemented business subset and calendar-origin limitations corrected in alpha 4.

Historical results remain tied to their original code and application revisions.
For release checks and artifact promotion, use the [release process](releases.md).
