Alpha 5 for CPython 3.12 on Linux and macOS, under the MIT license.

The installation tag now includes the public HTTPS/uv setup, MIT package metadata
and portable agent skill. Existing uv applications add workflow-sim as a dev
dependency. README, skill and package version all select `v0.1.0a5`.

The Celery retry example has a real negative control: mutating the order payload
before retry produces ASSERTION_FAILED on exact delivered content. The corrected
case preserves that content and an older delivery. The adapter guide explains
real task publication, modeled broker behavior and required client dependencies.

CLI exit codes and same-configuration evidence comparison are documented. Private
historical consumer evidence is labeled separately from public release evidence.
See [migration and scope](https://github.com/karthiksraju/workflow-sim/blob/v0.1.0a5/docs/alpha5.md).

Runtime scheduling, queue semantics and result schema are unchanged from alpha 4.
Release gates cover installed packages on Linux/macOS, minimal consumers, seven
fixed/broken example pairs, four real Linux prefork/Redis comparisons and six
runtime mutations. This is not a new full private application validation.

Assets are promoted from the tested commit's CI without rebuilding. Verify them
with `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c SHA256SUMS` on macOS).
