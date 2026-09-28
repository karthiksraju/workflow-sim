# Launch preparation

This is the execution checklist for alpha 3. Alpha 1/2 source, tags and artifacts
remain immutable. Publication stays private until ownership/license and public
visibility are explicitly settled.

- [x] Six installed, runnable domain examples: billing, fulfillment, ingestion,
  document/AI processing, monitoring and meetings. Each has realistic existing
  state, a named failure injection, fixed/broken behavior and final-state checks.
- [x] Explain fixture provenance and model limits. Reference workflows must not be
  described as third-party production incident reproductions.
- [x] Generated lifecycle tests and comparisons with ordinary asyncio.
- [x] Real prefork Celery + isolated Redis contract checks, including retry/canvas,
  JSON messages, duplicate delivery, worker-loss redelivery and expiry.
- [x] Simulator mutation campaign: baseline passes, deliberate defects are caught
  through assertions; import errors/timeouts do not count as killed mutants.
- [x] CI gates and promoted evidence artifacts for package, examples and confidence.
- [x] Illustrated example catalog, architecture/validation updates, migration notes,
  contributor/testing recipes, issue intake and a factual launch-post draft.
- [ ] Clean installed-package Linux/macOS validation; verify confidence reports and
  preserve exact versions, code hashes, commands and external-contract limitations.
- [ ] Publish verified private alpha 3 and update the consumer pin if runtime
  identity is preserved. No production merge/deploy, external messages or public
  visibility changes are part of preparation.

Tests and confidence scripts are explicitly requested deliverables for this
launch. Keep transient harnesses, raw local logs and mutation worktrees outside the
repository; maintain the reusable regression/CI gates in the library.
