# Working on workflow-sim

The library runs real workflow code under controlled time and failures. Its value
is a trustworthy verdict. A false PASS is a correctness defect, even when the
business assertions match.

## Preserve the boundary

- The runtime owns scheduling, execution lifetime and evidence. Adapters own
  application code, external models and business assertions. Keep application
  imports and provider-specific behavior out of the runtime.
- Account for every execution and publication path. Unsupported behavior,
  unfinished work and execution errors must remain visible when exceptions are
  caught or the entry coroutine returns.
- Keep patches inside the public runner's worker process and preserve cleanup on
  every exit path. Adapters are trusted code; containment is not a security sandbox.
- Add shared abstractions for demonstrated consumer needs. Queue independence is
  deferred; Celery is the current backend.

## Read for the change

- API, CLI, verdict or evidence changes: read [contracts](docs/contracts.md).
  Preserve request/result identity and update migration notes for changed meaning.
- Scheduling, ownership or backend changes: read [architecture](docs/architecture.md)
  and the [boundary decision](docs/adr/0001-alpha-boundary.md).
- Tests or examples: follow [contributor checks](CONTRIBUTING.md) and
  [confidence recipes](docs/confidence.md). Exercise real control flow, assert
  resulting content/state, and use an independent expected result. Demonstrate
  that the plausible bug fails the intended assertion.
- Dependencies, compatibility or releases: follow [release gates](docs/releases.md).
  Preserve pinned consumer counterexamples and promote the artifact actually tested.

## Finish the work

Use a feature branch and PR. Keep disposable harnesses, logs and generated results
outside the repository unless requested. Commit and push completed work; record
remaining work durably. Report the tested revision, relevant results and unverified
external contracts. Timeline coverage, business coverage and live integration
coverage are separate claims.

## Write for the reader

Lead with the behavior or decision. Keep only sentences you can explain and support;
label proposals and uncertainty. Link to authoritative detail instead of repeating
it. Preserve qualifications when shortening. Use diagrams to explain relationships,
not decorate a page. Review the final text for meaning, then remove repetition.
