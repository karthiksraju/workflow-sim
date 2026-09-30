# Contributing

Use a feature branch and PR. Start with the observable behavior that should change
and a test that would catch the bug. Agent instructions are in [AGENTS.md](AGENTS.md).

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and work on
Linux or macOS. `.python-version` selects CPython 3.12; uv manages `.venv` without
shell activation. From the repository root:

```sh
uv sync --locked
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --locked pytest -q
uv build
uv run --locked python -m twine check --strict dist/*
uv run --locked python scripts/check_package.py
```

To check another supported interpreter, use `uv sync --locked --python 3.14`,
then `uv run --locked --python 3.14 pytest -q` (or substitute `3.13`).
CI tests all three minors on Linux and macOS.

CI installs the built wheel and runs tests outside the checkout. Its later
commands use `uv run --no-sync` to avoid replacing that wheel with editable source.

Test behavior through the public runner when possible. Mock external services,
clocks and storage at the adapter boundary, not internal functions that decide
correctness. Describe the plausible bug caught by a test. For important fixes,
introduce that bug temporarily and confirm the assertion fails. Keep the mutation
or a reproducible negative control when it is useful.

Core changes also require the inherited clock/scheduler/crash suite. Tests verify
time and final state with independent arithmetic/models; do not compute expected
values from the implementation under test. Do not shrink a failure into a vacuous
check. Wall-time watchdog failures need diagnosis before changing thresholds.

Fixtures must name their source and be free of credentials/customer data. Raw
result files can contain application content; sanitize them before attaching them
to issues. Every PR states what external contracts remain unverified.

Update CHANGELOG and migration notes when behavior changes. Keep application
imports in adapters. @karthiksraju owns review; check CI before merging because
CODEOWNERS alone does not enforce approval.

`uv.lock` is the single development lock. Development tools live in the `dev`
dependency group. Use `uv add --dev <package>` to add a tool, or
`uv lock --upgrade-package <package>` for a targeted update; commit pyproject/lock
changes together and rerun both platforms. `uv sync --locked` rejects stale locks.
The lock validates one dependency set; consumers still use the package's bounds.

## Confidence gates

Run `uv run --locked python scripts/check_examples.py --output /tmp/workflow-examples` and
`uv run --locked python scripts/check_mutations.py --output /tmp/workflow-mutations`. Linux CI also
runs shared task bodies on real prefork Celery with isolated Redis; see
[reproduction commands and limits](docs/confidence.md). These reusable gates are
part of the library. Keep generated results and disposable harnesses outside the
source tree unless requested. New examples need corrected and broken versions,
fixture provenance, exact state/content checks and a documented boundary contract.

## Documentation and PRs

State what changes for the reader and why. Support claims with the relevant test,
contract or evidence record; keep assumptions and gaps visible. Remove repeated
explanations and link to their source. A diagram should explain a relationship or
sequence that takes longer to follow in prose. Review every sentence for meaning
before sharing it. Preserve historical results under their tested revisions.
