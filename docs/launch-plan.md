# Launch status

Alpha 4 stability fixes are merged. The release is a private draft. Public
licensing, distribution rights and tester access still need the owner's decision.

## Ready for review

- Six synthetic domain examples, each with a corrected and deliberately broken
  version, fixture provenance and final-state assertions.
- Linux/macOS installed-package checks, comparisons with ordinary asyncio and
  real prefork Celery/Redis, and targeted simulator mutations.
- Alpha 4 publication guards, calendar origins and pinned consumer comparison.
- [Tester guide](try-the-alpha.md), [evidence and limits](validation.md), feedback
  forms and [launch copy](launch-post.md).

## Before inviting public testers

1. Settle licensing and distribution rights, then choose repository visibility or
   a named-tester access route. Follow the [release gates](releases.md).
2. Review the alpha 4 draft's artifacts, hashes and notes before publishing it.
3. Put the accessible project link in the launch post and verify the installation
   path with the access a tester will have.

The independent corpus has 36 implemented business cases out of 240. Running the
remaining input timelines does not validate their business contracts. Queue
independence is deferred. Keep both limits explicit in launch claims.

The [alpha 3 release index](validation/alpha3-release.json) preserves the earlier
launch evidence; its tags and artifacts remain unchanged.
