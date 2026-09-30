# Security and data handling

Adapters execute trusted Python. Process separation provides cleanup and keeps
simulator patches out of the caller; it is not an OS sandbox. Native code and
evasive Python can bypass network/process guards. Workers can access files,
environment variables and credentials.

Use a disposable environment with synthetic data, without production credentials
or sensitive mounted directories. The library has no telemetry or automatic
artifact upload. Review results before sharing: assertions, logs and exceptions
can contain sensitive values.

Report vulnerabilities through [GitHub's private reporting form](https://github.com/karthiksraju/workflow-sim/security/advisories/new).
Keep credentials, exploit details and customer artifacts out of public issues.

Security fixes target the latest alpha and receive a new immutable tag.
Existing tags and published artifacts are never overwritten.
