# Maintaining and releasing the alpha

@karthiksraju owns triage, compatibility and release approval. Prioritize false
PASS reports, containment failures and installation failures, then adapter gaps.
Reproduce the bug and show that the regression check detects it.

## The artifact that was tested is the artifact released

```mermaid
flowchart TD
    Commit["Release commit"] --> Linux["Linux<br/>build + test"]
    Commit --> Mac["macOS<br/>build + test"]
    Linux --> Gate{"All gates pass on<br/>tagged commit?"}
    Mac --> Gate
    Commit --> Confidence["Examples + real worker contracts<br/>generated tests + mutations"]
    Confidence --> Gate
    Gate -- No --> Stop["No release"]
    Gate -- Yes --> Promote["Promote tested artifacts<br/>wheel + sdist + reports"]
    Promote --> Draft["Draft prerelease<br/>assets + hashes"]
    Draft --> Owner["Owner review<br/>evidence + release notes"]
    Owner --> Public["Published alpha"]
```

Promote the artifacts tested by CI. The workflow creates a draft;
publication requires owner approval. No PyPI publisher is enabled.

## Release checklist

1. Update the version in pyproject.toml and `workflow_sim.__version__`, CHANGELOG,
   release notes, supported-runtime matrix and migration notes. Align the README
   pin and skill revision so the tag includes the instructions users follow.
   Do not reuse a tag.
2. Run CI on the release commit: installed-wheel conformance on Linux and macOS,
   strict package metadata, and examples in a minimal fresh environment. CI uploads
   wheel, sdist and test results with 30-day retention.
3. For runtime changes, compare the meeting consumer at its pinned application
   revision. Preserve known product failures and assertion identities. Record the
   wheel hash and application/library revisions. If the private consumer is
   inaccessible, record that check as not run and leave the release gate open;
   library checks do not replace it. Keep PR validation distinct from release readiness.
4. Commit/push, then create and push `v<version>` at that exact commit. In a fresh
   project, execute the README and skill fixed/broken commands against that tag;
   verify its version, license metadata and skill files, not just the checkout.
5. Dispatch **Draft alpha release** with that tag. Both platform jobs and the
   confidence job must pass on the tagged commit. The workflow promotes the tested
   Linux wheel/sdist, platform reports, example/confidence archives and SHA256SUMS
   into a draft GitHub prerelease.
6. Review artifact hashes and notes, then publish the approved draft prerelease.
   The repository is already public under the [MIT license](../LICENSE), with
   [private vulnerability reporting](../SECURITY.md) enabled.

No PyPI publisher is configured. Before adding one, verify the distribution name
is available and configure a protected environment and Trusted Publisher. Public
GitHub access and an MIT license do not require publishing to PyPI.

Packaging follows [PyPA's pyproject guidance](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).
A future PyPI workflow should use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Compatibility and dependency changes

Support only CPython 3.12 and the two tested OS families for this alpha. Add a
runtime only after clock boundary, crash-return, pool ownership and process cleanup
probes pass there. These tests exercise CPython internals, so installing successfully
is insufficient. Celery is an explicit dependency until a second real backend
justifies a separate backend contract.

Dependabot updates `uv.lock`. Review the resolved changes and
run the same release checks. Record schema changes separately from package version.
A fix that changes evidence intentionally must include migration guidance for
stored records. Retain old tagged source and assets so previous evidence remains
reproducible.

## Tester intake

Use the [feedback form](https://github.com/karthiksraju/workflow-sim/issues/new?template=alpha-feedback.yml)
for workflow type, environment, setup friction and surprising verdicts. Ask for a
small sanitized adapter and its provenance. Track missing boundary contracts
separately from runtime defects.

A useful trial ends with a real workflow assertion that catches the intended bug.
Launch claims should state the support matrix, trusted-adapter requirement and
model limits; see the [launch checklist](launch-plan.md).

## Current repository enforcement

CI and the release verifier enforce artifact promotion checks. Branch-protection
setup was rejected while the repository was private. Public visibility does not
automatically configure protection. Check CI before merging: CODEOWNERS and the
PR template alone do not prevent an unchecked merge.

For an examples/docs-only release, compare every runtime module byte-for-byte
against the prior consumer pin, then verify the new wheel identity and focused
consumer loader/replay tests. Record that narrower scope explicitly; do not call
it a new full application validation. See [confidence recipes](confidence.md).
