# Security and data handling

This alpha runs trusted Python workflow adapters. Process separation is for
repeatable tests and cleanup; it is not an OS sandbox. Network/process guards can
be bypassed by native code or deliberately evasive Python. Filesystem access,
environment variables and credentials remain available to the worker.

Run in a disposable environment with synthetic data, without production
credentials or sensitive mounted directories. No telemetry or automatic artifact
upload is built into the package. Assertions, logs and exception messages can
contain sensitive values; review/redact results before sharing them.

For a vulnerability, contact repository owner @karthiksraju privately through your
existing internal channel. Do not put credentials, exploit data or customer
artifacts in a public issue. Public private-reporting infrastructure will be
configured before opening the repository.

The currently supported version is the latest internal alpha only. Fixes require a
new immutable alpha tag; tags and published artifacts must never be overwritten.
