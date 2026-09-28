# Alpha validation

The library passes **266 tests on both hosted Linux and macOS**, using the installed
wheel outside its source tree. The exact meeting comparison preserves **102 of 102
scenario outcomes, assertions and application-state snapshots**. The full consumer
self-test run is still finishing; its wheel-provenance test fix is already verified.

| Evidence | Verified | What this does not establish |
| --- | --- | --- |
| Library conformance | 266 tests per platform; local run also passes | Every possible interleaving or Python runtime |
| Installed distribution | Wheel built from sdist; strict metadata; fresh minimal environment | A live application integration without an adapter |
| Independent workflows | Async retry/deduplication and Celery retry payloads | Real broker or provider behavior |
| Negative controls | Duplicate delivery, shifted timer, stalled worker, malformed result and blocked boundary rejected | The completeness of an application's business assertions |
| Meeting extraction | 102 cases: identical checks, health, Celery records, clock jumps and state | Fixes for existing application counterexamples |
| Live external contracts | Not part of this extraction | Provider/database correctness or production certification |

Latest completed runtime CI:
[Linux and macOS run 36433939424](https://github.com/karthiksraju/workflow-sim/actions/runs/36433939424).
Documentation additions also passed
[run 36435066053](https://github.com/karthiksraju/workflow-sim/actions/runs/36435066053).

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

## Distribution status

Personal repository: private. Public license and visibility are pending. No PyPI
publisher or telemetry is enabled. Draft releases promote the tested CI artifacts.
Branch protection is unavailable on this private repository under the current
GitHub plan; [release and maintenance controls](releases.md) distinguish automated
checks from owner-enforced review.
