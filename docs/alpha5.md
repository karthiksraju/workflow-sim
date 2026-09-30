# Alpha 5: a consistent public installation

Alpha 5 is the first tag containing the MIT license and package metadata, uv
setup, and `skills/workflow-sim/SKILL.md`. The README and skill both select
`v0.1.0a5`. Earlier tags remain immutable; their private-era metadata and missing
skill directory make them unsuitable as the default onboarding path.

For an existing uv application, install with `uv add --dev` using the README's
HTTPS URL. This keeps the test tool out of runtime dependencies, but dev groups
still share version resolution. Use a separate environment when application
constraints conflict. Retain both `pyproject.toml` and `uv.lock` changes.

The Celery example now has a broken mode that damages an order's items before
retry publication. Retry lineage still matches; the exact delivered-content
assertion rejects the bug. It preserves an older delivery in both runs. The
[adapter guide](adapters.md#existing-celery-applications) covers using an
application's real registered tasks and required client dependencies.

## Compatibility and evidence

The scheduler, runner, queue implementation and result schema are unchanged from
alpha 4. All 12 runtime modules other than the version module are byte-identical;
only the version and example/catalog source change. Support remains CPython 3.12
on Linux/macOS. Newer Python support is separate from this onboarding release.

Release gates exercise the installed package on both platforms, six domain
examples plus the Celery fixed/broken pair, real Linux Celery/Redis comparisons
and six simulator mutation controls. Downloadable release evidence records the
actual results and artifact hashes. These checks do not repeat the full private
meeting-consumer suite or certify live provider contracts. Prior consumer results
remain historical; [validation](validation.md) distinguishes available evidence
from archives still held in the alpha 4 draft.

`evidence_sha256` can change when a package version changes. Compare repeats under
identical code, dependencies and configuration; investigate differences without
assuming arbitrary application timing is deterministic.
