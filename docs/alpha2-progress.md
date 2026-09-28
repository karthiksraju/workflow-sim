# Alpha 2 adversarial fixes

Target: the 11 verified findings in the independent review of 8ef93ca.
Original release v0.1.0a1 remains immutable. Corrections will ship as v0.1.0a2.

- Celery F03–F07, F09: completion/publication failures affect task state; one signature publication path preserves options; retry signatures retain continuations; every delivery decodes the original serialized message; unsupported options remain visible when caught; completed/crashed executions disarm limits. Regression and existing conformance/property selection: 46 passed across two runs.
- Async F01–F02 and setup F08: descendant ownership, retained/unhandled errors, assertion-stage health refresh, lifecycle thread guards, live-thread crash fencing. Controls include caught exceptions, successful children and cancelled timers.
- CLI F10 and source archive F11: parsing errors invalidate explicit output and exit 4; safe-path controls avoid guessed targets; source archives contain the tested dependency lock.
- All 311 library tests pass locally (44 new regressions/controls). Wheel/sdist strict metadata and fresh minimal-install checks pass.
- Remaining: installed-wheel Linux/macOS CI, meeting consumer upgrade checks, final evidence and private alpha release.

Baseline against old code: 22 new assertions failed, two controls passed. Reproducers use synthetic observable state and Celery's installed 5.6.3 producer API, not mocks of runtime internals. Full evidence will replace this working progress note before release.
