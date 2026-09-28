# Alpha 2: adversarial review corrections

The independent review of `8ef93ca` reproduced eleven defects in the released
`0.1.0a1` wheel. Three allowed PASS despite unfinished or failed execution. The
corrections below preserve the public API and result schema 1, while correcting
execution accounting and Celery behavior. Never use the old release as a standalone
approval gate.

| Finding | Corrected behavior | Permanent regression |
| --- | --- | --- |
| F01: async descendants disappear | Child tasks, timers and external waits remain in flight after the parent returns. Crash fencing uses live thread identities. | `test_async_descendants_remain_in_flight`, `test_crash_after_parent_returns_fences_async_descendant` |
| F02: unhandled asyncio errors disappear | Unhandled callbacks and unretrieved task/future exceptions affect health, including retained objects. Caught errors remain valid. | `test_unhandled_async_failures_veto_matching_checks`, `test_async_error_observation_and_descendant_completion` |
| F03: continuation publication looks successful | Serialization and completion-hook failures enter task failure evidence. | `test_continuation_serialization_failure_is_execution_failure`, `test_completion_hook_failure_is_not_swallowed_by_future_callback` |
| F04: retry loses continuations | Retry signatures preserve links, error links and remaining chain metadata without double publication. | `test_continuations_deliver_after_retry`, `test_retry_compositions_preserve_payload_order_identity_and_errbacks` |
| F05: linked signature options disappear | Signatures and direct calls use one option-validation/publication path, including expiry. | `test_expiry_applies_to_direct_and_linked_deliveries` |
| F06: redelivery reuses mutated payload | Each delivery decodes the original serialized message. | `test_redelivery_uses_original_message_payload`, `test_duplicate_delivery_has_independent_decoded_payload` |
| F07: caught unsupported calls vanish | Unsupported Celery operations leave a durable signal shared with the engine. | `test_unsupported_celery_is_sticky_on_every_publish_path` |
| F08: setup threads escape accounting | Public workers reject unowned threads/pool submissions throughout adapter execution. | `test_unowned_setup_threads_are_explicitly_unsupported`, `test_catching_unowned_lifecycle_work_does_not_hide_it` |
| F09: completed tasks retain time limits | Deadline registration and completion are synchronized; success/crash removes owned limits. | `test_success_disarms_task_limits`, retry-with-limits composition |
| F10: parse errors leave stale PASS | Explicit output paths are invalidated and errors exit 4, including malformed numeric arguments. | `test_cli_parse_failures_remove_stale_pass`, safe-path controls |
| F11: sdist omits contributor lock | The source archive contains the exact tested dependency lock. | `scripts/check_package.py` |

Tests live in [async/CLI regressions](../tests/test_adversarial_regressions.py) and
[Celery regressions](../tests/test_celery_regressions.py). Adapter fixtures are
synthetic in-memory workflows; Celery retry expectations follow the installed
5.6.3 `Task.retry`, `signature_from_request` and `Context.as_execution_options`
producer implementations. No internal scheduling functions are mocked.

## Evidence migration

Keep `v0.1.0a1` source, assets and prior evidence intact. Install/pin `v0.1.0a2`,
record the new wheel/source hashes, and rerun scenarios. Old verdicts cannot be
reinterpreted without execution: they lack the newly captured health information.
Expected corrections include PASS becoming INCOMPLETE/HARNESS_ERROR/UNSUPPORTED,
and false INCOMPLETE disappearing when successful tasks disarm their limits.

Result field names and schema remain unchanged. Failure collections gain evidence,
so consumers must not assume that a successful entry coroutine proves completion.
Use ordinary exception handling to consume expected failures. Use `ctx.at` for
work and the owned asyncio pool APIs for thread work; starting raw setup threads
is explicitly unsupported in the public runner. CLI option abbreviations are no
longer accepted.

The model still does not certify live brokers, databases, provider contracts,
arbitrary native code, or every scheduling interleaving. New regressions establish
these concrete corrections, not universal workflow correctness.

## Validation

The old-code regression baseline produced 22 failures and two passing controls.
Additional controls cover retained versus consumed exceptions, completed child
work, cancelled timers, late child crashes, assertions that create async failures,
retry/error-link composition, duplicate payloads, and CLI path interpretation.
Current run and release evidence is tracked in [alpha delivery progress](alpha2-progress.md).
