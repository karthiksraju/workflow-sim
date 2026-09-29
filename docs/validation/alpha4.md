# Alpha 4 stability verification

The publication guards and configurable origin pass the installed-wheel checks
below. The queue-independent refactor is not part of this release.

| Check | Observed result | Limit |
| --- | --- | --- |
| Existing library suite | 325 pass on Linux; 325 on macOS | CPython 3.12 and the recorded dependencies |
| Added local acceptance suite | 41 pass on macOS; 41 on Linux | Synthetic state, real producer APIs; harness retained outside the PR |
| Old-wheel negative control | 18 publication regressions fail on unchanged alpha 3 | Demonstrates the original missing-publication accounting |
| New runtime mutations | Guard removal causes 21 assertion failures; ignored origin causes three | Passing 25-case baseline; no import error or timeout counted as detection |
| Existing confidence CI | Four actual prefork/Redis comparisons; six curated runtime mutations detected | Recorded Celery/Redis/JSON configuration only |
| Shipped examples | Six fixed PASS, six broken ASSERTION_FAILED | All 12 evidence digests identical to alpha 3 |
| Independent corpus | 1,026 expected outcomes with the original 2026 clock origin | 36 implemented business cases; 204 remain unimplemented |
| Pinned meeting application replay | All 102 outcomes, business checks/state, health and execution reports unchanged | Existing application failures remain; this is upgrade compatibility |

Initial CI: [36526803415](https://github.com/karthiksraju/workflow-sim/actions/runs/36526803415),
runtime source `459c2fd5129dcc7f71dedd0523493a13d283414a`. Both CI wheels contain
exactly the runtime files tested locally. Release promotion reruns the same CI
gates on the tagged commit, then promotes its tested artifacts without rebuilding.

## What the new checks establish

The acceptance harness publishes during setup, callbacks and assertion evaluation.
Catching publication exceptions still yields UNSUPPORTED, and the real in-memory
Kombu queue remains empty. Registered signatures still deliver exact JSON content.
Parent process methods and sequential in-process engine patches are restored.
The AMQP class-entry probe checks rejection before channel access; the real-worker
comparison uses Redis, not a live AMQP broker.

Clock checks cover offset normalization, leap-day dates, fractional origins,
microsecond sleeps, absolute Celery ETAs, repeatable evidence, tampered
configuration and invalid CLI input replacing an old PASS. The default request
shape is retained. Three additional origin probes at years 1, 1900 and 9998
preserve the exact six-digit wall-clock origin.

## Independent corpus scope

The frozen corpus is version 1.0.0, revision `e3d669f`, with all 35 files and its
manifest verified against the original hashes. The same local reference handlers
used for alpha 3 execute against alpha 4; inputs and expected business results
are unchanged. The bridge now supplies `start_at` from each case's clock origin.

The 1,026 runs comprise 480 positive timelines, 480 payload-corruption controls,
20 alternate legal equal-time orders, 36 reference business cases, six deliberate
business bugs and four clock/health probes. The origin probe now passes; the late
microsecond, outstanding-work and unobserved-error controls retain their expected
non-PASS outcomes. These are not 240 validated production workflows. See the
[original business coverage boundaries](corpus-alpha3.md).

## Meeting consumer compatibility

Replay uses the exact historical application revision
`232666fde4ef5c9e08f18fc714d75c1899afc84d`, with the candidate wheel overlaid through
the consumer's existing historical-run script. The 102 outcomes remain **73 PASS
and 29 ASSERTION_FAILED**, including the same nine pending-fix failures. Every
business check, health verdict, application state and execution report matches.

99 ledgers match byte-for-byte. The remaining three differ solely in
`cal_ntfn_rescan_finished.data.duration_seconds`, an application-recorded real
performance metric: 0.006/0.005/0.004 seconds previously, 0.003 seconds on this run.
The comparison verifies every other ledger field is identical.

An initial replay used the current consumer application revision `64c0ef22`.
Its business behavior differs from the historical application, so that run is
retained only as unaligned diagnostic evidence. It is not used to claim a library
regression or compatibility. The current consumer branch/pin remains unchanged;
all candidate application checks use disposable worktrees.

## Evidence and maintenance

Local harnesses, mutation definitions, raw results and setup diagnostics are kept
outside the library PR. The private draft release's supplemental evidence archive
retains these checks for reproduction. Existing permanent CI gates remain in the
repository; the new local acceptance suite is additional release verification,
not a claim of increased permanent CI test count.

Public distribution still requires the owner's license/visibility decision. The
runtime remains an alpha with documented boundaries, not proof of production
safety or certification of external provider contracts.
