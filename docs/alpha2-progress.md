# Alpha 2 verification record

Historical record. See [current validation](validation.md) for alpha 4.

All eleven independently reproduced findings are fixed. The final runtime passes
316 library tests on Linux and macOS, and all 19 original reviewer probes/controls
pass their corrected expectations. The old-code negative controls reproduce the
defects; tests use observable content and state.

Consumer evidence includes 976 passing tests and six strict application xfails on
the initial corrected wheel, then 192 affected-module tests and all 102 scenario
comparisons on the final bridge correction. All nine policy mutations were caught.
Every scenario's business checks, health verdicts and application state remain
unchanged. The same nine pending application failures are preserved.

See [validation](validation.md) for exact revisions, artifact hashes, scope and
intentional evidence differences. [Migration](alpha2.md) explains why old results
must be rerun. Alpha 1 remains immutable; alpha 2 is promoted from CI artifacts
only after both platform checks pass on its exact release commit.
