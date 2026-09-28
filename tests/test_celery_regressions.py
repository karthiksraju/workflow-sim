"""Public-API regression witnesses from the independent 2026-09-28 review.

Adapters use synthetic in-memory state and Celery's documented task/signature API.
No internal runtime functions are mocked. Retry fixtures follow Celery 5.6.3's
Task.retry/signature_from_request and Context.as_execution_options producer code.
The original witnesses all failed on v0.1.0a1; checks below assert corrected behavior.
"""
from pathlib import Path
import json
import subprocess
import sys

import pytest
from workflow_sim import run

ADAPTERS = Path(__file__).parent / 'adapters'


def scenario(module, mode):
    return run(module + ':build', inputs={'mode': mode}, duration=10,
               wall_timeout=5, project_dir=ADAPTERS)


def test_continuation_serialization_failure_is_execution_failure():
    result = scenario('celery_adapter', 'broken_link_payload')
    assert result['outcome'] == 'HARNESS_ERROR', result
    failures = result['evidence']['report']['task_failures']
    assert failures and 'EncodeError' in str(failures)
    assert result['evidence']['checks'][0]['actual'] == [['first-completed']]


@pytest.mark.parametrize('mode', ['retry_chain', 'retry_link', 'plain_chain'])
def test_continuations_deliver_after_retry(mode):
    result = scenario('celery_adapter', mode)
    assert result['outcome'] == 'PASS', result
    check = result['evidence']['checks'][0]
    assert check['actual'] == check['expected']
    assert check['actual'][-1] == ['last', 'payload']
    assert check['actual'].count(['last', 'payload']) == 1


@pytest.mark.parametrize('mode', ['link_expiry', 'direct_expiry'])
def test_expiry_applies_to_direct_and_linked_deliveries(mode):
    result = scenario('celery_adapter', mode)
    assert result['outcome'] == 'PASS', result
    assert ['last', 'expired'] not in result['evidence']['checks'][0]['actual']
    assert any(e['kind'] == 'task' and e['data']['state'] == 'REVOKED'
               for e in result['evidence']['ledger'])


@pytest.mark.parametrize('module,mode', [('celery_adapter', 'link_unsupported'),
                                        ('review_adapter', 'celery_caught_unsupported')])
def test_unsupported_celery_is_sticky_on_every_publish_path(module, mode):
    result = scenario(module, mode)
    assert result['outcome'] == 'UNSUPPORTED', result
    assert result['evidence']['unsupported']


def test_redelivery_uses_original_message_payload():
    result = scenario('crash_adapter', 'crash-redelivery')
    # The injected crash remains visible; it does not excuse corrupt message data.
    assert result['outcome'] == 'HARNESS_ERROR', result
    for check in result['evidence']['checks']:
        assert check['actual'] == check['expected'], check


def test_success_disarms_task_limits():
    result = scenario('review_adapter', 'celery_limit')
    assert result['outcome'] == 'PASS', result
    assert result['evidence']['checks'][0]['actual'] == ['payload']
    assert result['evidence']['report']['pending_items'] == []


@pytest.mark.parametrize('mode', ['three_stage', 'terminal_error', 'retry_with_limits'])
def test_retry_compositions_preserve_payload_order_identity_and_errbacks(mode):
    result = run('celery_adapter:composed', inputs={'mode': mode}, duration=10,
                 project_dir=ADAPTERS)
    assert result['outcome'] == ('HARNESS_ERROR' if mode == 'terminal_error' else 'PASS'), result
    for check in result['evidence']['checks']:
        assert check['actual'] == check['expected'], check
    assert result['evidence']['report']['pending_items'] == []


def test_duplicate_delivery_has_independent_decoded_payload():
    result = run('celery_adapter:duplicate', duration=10, project_dir=ADAPTERS)
    assert result['outcome'] == 'PASS', result
    assert result['evidence']['checks'][0]['actual'] == [['original'], ['original']]


def test_completion_hook_failure_is_not_swallowed_by_future_callback():
    result = run('celery_adapter:completion_hook', duration=10, project_dir=ADAPTERS)
    assert result['outcome'] == 'HARNESS_ERROR', result
    assert 'completion hook broke' in str(result['evidence']['report']['task_failures'])



def test_link_preserves_frozen_task_identity_and_priority():
    result = run('celery_adapter:link_identity', duration=10, project_dir=ADAPTERS)
    assert result['outcome'] == 'PASS', result
    check = result['evidence']['checks'][0]
    assert check['actual'] == check['expected']
    assert any(e['kind'] == 'task' and e['data']['task_id'] == 'frozen-child-id'
               for e in result['evidence']['ledger'])
