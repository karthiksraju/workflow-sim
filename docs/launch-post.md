# Launch copy — draft, not published

Publish only after the owner chooses a license/distribution model and makes the
repository or a tester-access route available. The current repository is private.

## Short post

I'm building workflow-sim: test Python async/Celery workflows with virtual time,
retries and injected failures, then check what they actually wrote or delivered.

Looking for alpha testers working on billing, fulfillment, data pipelines, AI
processing, monitoring or meetings. CPython 3.12, Linux/macOS.

## Follow-up / landing-page copy

Six runnable examples each include a deliberate bug and a corrected version.
The simulator is checked against ordinary asyncio and real Celery workers, plus
tests that deliberately break the simulator itself.

Bring one workflow and one failure you struggle to reproduce. Start with the
example closest to it, connect your real code, and tell us where setup or the
results fall short.

This is an alpha testing tool with modeled external systems. It does not prove
production safety or explore every concurrency schedule. AI examples test workflow
behavior, not model answer quality.

Links after access is settled: [repository](https://github.com/karthiksraju/workflow-sim),
[examples](examples.md), [tester guide](try-the-alpha.md), [evidence and limits](confidence.md).
