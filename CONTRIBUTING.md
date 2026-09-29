# Contributing

Use a feature branch and PR. Start with the observable behavior that should change
and a test that would catch the bug. Agent instructions are in [AGENTS.md](AGENTS.md).

Use CPython 3.12 on Linux or macOS:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m build
python -m twine check --strict dist/*
python scripts/check_package.py
```

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

`requirements-dev.lock` is the tested development dependency set. Regenerate with
`uv pip compile pyproject.toml --extra dev --universal --generate-hashes -o
requirements-dev.lock`; review upgrades and rerun both platforms. Consumers use
the bounded dependencies in pyproject.toml. A lock refresh is not proof that every
version in those bounds works.

## Confidence gates

Run `python scripts/check_examples.py --output /tmp/workflow-examples` and
`python scripts/check_mutations.py --output /tmp/workflow-mutations`. Linux CI also
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
