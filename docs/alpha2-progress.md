# Alpha 2 adversarial fixes

Target: the 11 verified findings in the independent review of 8ef93ca.
Original release v0.1.0a1 remains immutable. Corrections will ship as v0.1.0a2.

- Celery F03–F07, F09: completion/publication failures affect task state; one signature publication path preserves options; retry signatures retain continuations; every delivery decodes the original serialized message; unsupported options remain visible when caught; completed/crashed executions disarm limits. Regression and existing conformance/property selection: 46 passed across two runs.
- Remaining: async descendants/unhandled errors F01–F02; setup ownership F08; CLI stale artifacts F10; source distribution lock F11; full installed-package/Linux/macOS validation and release.

Baseline against old code: 22 new assertions failed, two controls passed. Reproducers use synthetic observable state and Celery's installed 5.6.3 producer API, not mocks of runtime internals. Full evidence will replace this working progress note before release.
