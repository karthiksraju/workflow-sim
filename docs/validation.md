# Alpha validation

## Alpha 2 adversarial corrections

All eleven independently reproduced review findings are corrected. **316 tests**
pass locally and in the installed-wheel Linux/macOS
[final runtime CI](https://github.com/karthiksraju/workflow-sim/actions/runs/36448361137)
at `c9ba673c373bffc7b076753d86815560719117df`. That adds 49 regressions and controls
to the original 267 tests. The release workflow repeats the checks on the exact
release commit and promotes those tested artifacts.

| Evidence | Result | Scope |
| --- | --- | --- |
| Old-code negative baseline | 22 failures, two controls passed | New regressions detect the original defects |
| Final library suite | 316 passed on Linux and macOS | CPython 3.12, real scheduler/Celery/pool/process boundaries |
| Original independent review adapters | 19 witnesses/controls pass corrected expectations | Unmodified review probes, final installed CI wheel |
| Coroutine handoff stress | Old wheel runs a t=6 callback at t=8; correction passes | Real threads, frequent switching, injected crash; no scheduler mocks |
| Packaging | Strict wheel/sdist metadata and minimal-install examples pass | Source archive includes the exact contributor lock |

[Finding-to-fix map and migration](alpha2.md) explains the changed verdicts.
[Original review replay index](validation/alpha2-review-replay.json) records each
before/after verdict and evidence hash. Tests verify resulting content and pending
work, not only success codes. Synthetic boundary fixtures use the real Celery 5.6.3
producer API. Live brokers, provider schemas, databases and arbitrary application
adapters remain outside these checks.

Unobserved asyncio error detection deliberately targets CPython 3.12. Public
workers enforce strict ownership throughout adapter execution; the advanced
in-process engine retains its explicit opt-in lifecycle guard for compatibility.
This release does not extend the support matrix or claim every possible thread
interleaving.

### Consumer regression checks

The final runtime wheel passed **192 consumer tests** in clock/Celery execution,
engine conformance, pool ownership, evidence, harness provenance and historical
proof modules. This includes real historical before-fail/after-pass execution and
rejection of altered runtime/proof evidence. The
[consumer check index](validation/alpha2-consumer-targeted.json) records the exact
library and application revisions and wheel hashes.

The final 102-scenario comparison at application `232666fd` uses that same runtime
wheel. **All 102 outcomes, complete business checks, health verdicts and application
state match** the prior alpha 1 candidate. All runs are execution-healthy.

| Scenario set | Alpha 1 and alpha 2 | Gate |
| --- | --- | --- |
| Established, 31 | 28 PASS / 3 declared counterexamples | PASS |
| Pending fixes, 32 | 23 PASS / 9 unresolved application failures | FAIL, preserved |
| Calendar corpus, 39 | 22 PASS / 17 declared counterexamples | PASS |

[Comparison index](validation/alpha2-meeting-comparison.json) records artifact and
source identities, per-case assertion failures and raw-record hashes. The nine
pending failures concern ASR fallback retry generations and reschedule CRM/email
carryover; this library release does not fix those application policies.

Execution records deliberately differ. Completed tasks cancel their deadline
callbacks, reducing steps and clock jumps and removing stale pending limits.
Three recorder cases' Celery records differ only in task IDs, because frozen
continuation IDs now survive publication. Only seven ledger hashes are identical;
we retain the original records rather than normalize away execution differences.

The full 982-test consumer run on the initial alpha 2 wheel remains in progress.
The 192-test rerun and 102-case comparison above use the final bridge correction.

The [alpha 1 extraction record](validation-alpha1.md) remains available for
provenance. Its passing tests preceded the defects found by the independent review.

## Distribution and limits

The repository and release remain private. Public licensing and visibility remain
owner decisions; no PyPI publishing or telemetry is enabled. The release workflow
promotes the tested wheel and source archive with both platform reports and
SHA256SUMS. See [release controls](releases.md) for compatibility policy and the
private repository’s branch-protection limitation.

Twelve Mermaid diagrams across library and consumer documentation have been
rendered. The new completion/ownership diagram was visually inspected, and local
documentation links were checked.
