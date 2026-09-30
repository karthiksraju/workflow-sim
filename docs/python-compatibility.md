# Python compatibility

Current source supports standard GIL-enabled CPython 3.12, 3.13 and 3.14 on
Linux and macOS. The `v0.1.0a4` tag is unchanged and still requires Python 3.12.
Newer-version support will ship in the next alpha; there is no new release yet.

To try current source after cloning the repository:

```sh
uv sync --locked --python 3.14
uv run --locked --python 3.14 workflow-sim workflow_sim.examples.retry:build --duration 10
```

Substitute `3.13` or `3.12` as needed. `.python-version` remains the contributor
default of 3.12, not a restriction on supported interpreters. In another uv
project, add this checkout with `uv add /absolute/path/to/workflow-sim` and retain
its exact commit alongside your results.

## Why support is bounded

Crash fencing uses `sys.monitoring`, introduced in Python 3.12. Python 3.13
removed the Python-level non-daemon thread shutdown registries. Owned pool workers
now start as daemon threads. Normal completion and cancellation still wait for
pool effects; only a hard-abandoned worker leaves the pool's join registries.

On 3.13 and 3.14, a pool with prestarted non-daemon workers is rejected before
submission, even if the application catches the exception. Create the executor's
workers inside the simulation. This chiefly affects advanced in-process engine
users; the public runner already rejects unowned setup threads. Python 3.12
retains its previous handling of prestarted pools.

Free-threaded builds, PyPy, Windows and Python 3.15+ are outside this support
matrix and the public runner rejects them. Each added minor needs the installed
wheel suite, exact-state examples, minimal consumer checks, real Linux
Celery/Redis comparisons and mutation checks. The release verifier requires all
six platform/runtime jobs and all three confidence jobs to pass.

The result schema and business assertion semantics are unchanged. Rerun an
adapter when changing Python: prior evidence describes its recorded interpreter
and dependencies, not every supported environment.
