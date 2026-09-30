# Launch status

The repository is public under the [MIT license](../LICENSE). Testers can install
from GitHub over HTTPS without credentials. Alpha 4 release assets remain a draft.

## Ready for review

- Six synthetic domain examples, each with a corrected and deliberately broken
  version, fixture provenance and final-state assertions.
- Linux/macOS installed-package checks, comparisons with ordinary asyncio and
  real prefork Celery/Redis, and targeted simulator mutations.
- Alpha 4 publication guards, calendar origins and pinned consumer comparison.
- [Tester guide](try-the-alpha.md), [evidence and limits](validation.md), feedback
  forms and the [agent skill](../skills/workflow-sim/SKILL.md).

## Before inviting public testers

1. Review the alpha 4 draft's artifacts, hashes and notes against the
   [release gates](releases.md) before publishing it.
2. Verify the documented HTTPS installation from a fresh environment.
3. Link the project, examples and feedback form in the launch post. State the
   tested runtime and model limits.

The independent corpus has 36 implemented business cases out of 240. Running the
remaining input timelines does not validate their business contracts. Queue
independence is deferred. Keep both limits explicit in launch claims.

The [alpha 3 release index](validation/alpha3-release.json) preserves the earlier
launch evidence; its tags and artifacts remain unchanged.
