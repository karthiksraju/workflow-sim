# Alpha 1 extraction validation (historical)


The following records describe the original extraction before the adversarial
review. They do not establish the alpha 2 corrections and are retained for provenance.

The library passes **267 tests on both hosted Linux and macOS**, using the installed
wheel outside its source tree. The exact meeting comparison preserves **102 of 102
scenario outcomes, assertions and application-state snapshots**. All consumer failures found during extraction have been resolved and their
affected modules rerun successfully.

| Evidence | Verified | What this does not establish |
| --- | --- | --- |
| Library conformance | 267 tests per platform; local run also passes | Every possible interleaving or Python runtime |
| Installed distribution | Wheel built from sdist; strict metadata; fresh minimal environment | A live application integration without an adapter |
| Independent workflows | Async retry/deduplication and Celery retry payloads | Real broker or provider behavior |
| Negative controls | Duplicate delivery, shifted timer, stalled worker, malformed result and blocked boundary rejected | The completeness of an application's business assertions |
| Meeting extraction | 102 cases: identical checks, health, Celery records, clock jumps and state | Fixes for existing application counterexamples |
| Live external contracts | Not part of this extraction | Provider/database correctness or production certification |

Latest completed runtime CI:
[Linux and macOS run 36436162068](https://github.com/karthiksraju/workflow-sim/actions/runs/36436162068)
on commit `b7ab27f21b31fb15174600259ef1bd6362b5efa4`. The draft release verifier
requires both platform jobs to pass again on the exact tagged release commit.

## Meeting comparison

The application revisions differ only by runtime extraction. The composed candidate
still contains the same product fixes as the previous validated candidate.

| Scenario set | Before extraction | After extraction | Gate |
| --- | --- | --- | --- |
| Established, 31 | 28 PASS / 3 declared counterexamples | Same | PASS |
| Pending fixes, 32 | 23 PASS / 9 unresolved product failures | Same | FAIL, intentionally preserved |
| Calendar corpus, 39 | 22 PASS / 17 declared counterexamples | Same | PASS |

All 102 runs are execution-healthy. No failed product assertion became PASS through
extraction. The remaining nine pending failures concern fallback retry generation
and reschedule carryover; this release does not claim to fix them.

[Machine-readable comparison](validation/meeting-extraction-20260928.json) records
application revisions, the pinned library artifact, per-case outcomes and assertion
counts, and hashes of the before/after records.

**100 ledger hashes match exactly.** Two differ only in the application's
`cal_ntfn_rescan_finished.data.duration_seconds` field (real elapsed time, 0.007
versus 0.003 seconds). Every other event field matches. Those values were retained
in the evidence, not normalized away to manufacture matching hashes. Adapter logs
of real elapsed time can make a ledger vary even when virtual execution and state
are identical.

## Test design and corrections

Clock tests compute expected instants with independent datetime/rational arithmetic.
Queue tests compare execution order and serialized payloads with a separate model.
Pool probes verify effects after hard abandonment and graceful cancellation.
Public API tests use real child processes and inspect payloads, assertion witnesses,
ledger content, repeat hashes, worker disappearance and absence of a delayed file write.

The first hosted macOS run exposed the inherited 500 ms total watchdog on a
10,000-iteration zero-delay stress test. Only that stress case now has a 5-second
wall bound; exact no-time-advance/yield-order assertions remain. A one-microsecond
timer mutation fails those assertions before the stress tail, and a stalled
coroutine still hits the unchanged default watchdog.

The meeting consumer's imported-file coverage test assumed loose `.py` files.
Its replacement binds zip-imported modules to the exact wheel in the harness
manifest. All 31 provenance tests pass, including modified-wheel rejection and
refusal of a preloaded package from another source.

## Full consumer suite and follow-up checks

The full 978-test consumer run completed in 30m12s: **958 passed, 6 strict
application xfails, 14 failures**. Those failures had two causes, both resolved:

- One imported-file coverage test did not understand zip-loaded wheel members.
  The replacement verifies the actual archive and its hash. The combined clock
  and provenance modules then passed **201 tests**, including four new negative
  controls for altered timers, stalled coroutines, wheel tampering and shadowing.
- Thirteen historical-proof checks shared a fixture that launched the system
  Python from a fresh worktree without its dependencies. It now explicitly uses
  the active test interpreter, as the other proof fixtures already did. The whole
  proof module then passed **78 tests**, including a real historical before-fail /
  after-pass pair and rejection of damaged proof records.

All **nine application policy mutations were caught** in the full run. The six
strict xfails describe existing application counterexamples; extraction did not
weaken or remove them. We retain the original full-run result rather than relabel
it as a wholly green rerun. Only affected modules were repeated after the fixes.

The final library's clock, engine, Celery driver, ledger, bridge and time seam are
unchanged from the kernel used in the 102-case comparison and full consumer run.
Later public-runner hardening is covered by the 267-test library suite. The final consumer pin verified all seven kernel/bootstrap module hashes and
passed **154 tests** for bindings, clock/Celery execution, evidence, provenance and
historical proofs against the exact released wheel.

## Documentation verification

Eleven Mermaid diagrams across the library and meeting integration guides were
rendered with Mermaid CLI and Chrome. The architecture, lifecycle, verdict and
retry figures were visually inspected; documentation links were checked. Mermaid
sources remain in Markdown so GitHub renders them and code changes can update them.

## Distribution status

Personal repository: private. Public license and visibility are pending. No PyPI
publisher or telemetry is enabled. Draft releases promote the tested CI artifacts.
Branch protection is unavailable on this private repository under the current
GitHub plan; [release and maintenance controls](releases.md) distinguish automated
checks from owner-enforced review.


## Created alpha artifacts

Tag `v0.1.0a1` points to `5bd5eea7ee39de10934a4b5b584cd6a70584e9ca`.
[CI on that exact commit](https://github.com/karthiksraju/workflow-sim/actions/runs/36436750104)
passed both platform jobs. The
[draft-release workflow](https://github.com/karthiksraju/workflow-sim/actions/runs/36437037368)
succeeded and promoted the tested wheel, source archive and both platform test
reports into a draft, then promoted it to the
[private internal prerelease](https://github.com/karthiksraju/workflow-sim/releases/tag/v0.1.0a1)
after verification. Downloaded assets passed all SHA256SUMS
checks. The wheel SHA256 is
`d04e0d5daa14839be3994e8600177b3b784067188b62cd4edc97820de58b569a`.

A fresh environment installed directly from the personal GitHub `v0.1.0a1` tag.
Running the installed CLI outside the checkout returned version `0.1.0a1`, a
`PASS` verdict and exactly one invoice (`invoice-42`, amount 1200) in the retry
example. This checks the private GitHub installation path as well as the released
workflow behavior; it requires repository access.
