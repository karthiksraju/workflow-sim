Internal alpha 3 for CPython 3.12 on Linux and macOS.

Six domain workflows are ready to run: billing, fulfillment, ingestion, document/AI
processing, monitoring and meetings. Every example includes a deliberately broken
control and assertions about resulting content/state. See the
[illustrated catalog](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a3/docs/examples.md)
and [tester guide](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a3/docs/try-the-alpha.md).

Release gates require:

- 325 installed-package tests on each supported OS, including generated asyncio
  comparisons and child lifecycle checks.
- Six fixed examples passing and six broken controls failing their intended checks.
- Four shared Celery task programs matching actual Linux prefork/Redis behavior,
  including worker-loss redelivery and original-payload preservation.
- Six targeted simulator mutations caught by behavioral assertions after a passing
  baseline. This is a curated campaign, not a whole-project mutation score.
- Strict package metadata and minimal dependency installation checks.

The execution runtime is byte-identical to alpha 2, apart from the package version.
Result schema remains 1. The new examples and Redis development dependency do not
change the runtime dependency set. Existing alpha 2 adapters need no migration.
Alpha 1 users should read the
[alpha 2 corrections](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a3/docs/alpha2.md)
and regenerate their evidence.

Examples are synthetic reference workflows, not six production integrations.
Meeting consumer integration evidence is recorded separately. Real-worker checks
cover the recorded Celery/Redis/JSON configuration; they do not certify every
broker, provider or database. A modeled crash remains visible as HARNESS_ERROR
while recovery effects are checked independently. See
[confidence and limits](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a3/docs/confidence.md).

Private distribution only; public licensing/visibility remains pending. Artifacts
are promoted from the exact release commit's CI, without rebuilding. Evidence
archives include full example results, real-worker records/logs and mutation
reports. From the downloaded asset directory, run `sha256sum -c SHA256SUMS`
(or `shasum -a 256 -c SHA256SUMS` on macOS).
