# workflow-sim

An internal alpha of a deterministic test runtime for asynchronous Python workflows.
Use it to reproduce retries, delayed tasks and worker failures against observable
state. Runs execute in disposable child processes with virtual time.

Extraction is in progress. The first release targets CPython 3.12 on Linux and
macOS. This is a simulator for testing production code, not a production task
orchestrator or a security sandbox for untrusted Python.

The repository is private while licensing and public distribution are settled.
No open-source license is granted yet. Package publication is deliberately blocked.

See [the extraction decision](docs/adr/0001-alpha-boundary.md) and
[the implementation checklist](docs/implementation.md).

