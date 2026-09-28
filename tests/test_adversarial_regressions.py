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


@pytest.mark.parametrize('mode', ['future_task', 'future_timer', 'blocked_task'])
def test_async_descendants_remain_in_flight(mode):
    result = scenario('review_adapter', mode)
    assert result['outcome'] == 'INCOMPLETE', result
    assert result['evidence']['report']['in_flight']
    assert not any(e['event_id'] in ('child-completed', 'timer-fired')
                   for e in result['evidence']['ledger'])


@pytest.mark.parametrize('mode', ['async_error', 'callback_error'])
def test_unhandled_async_failures_veto_matching_checks(mode):
    result = scenario('review_adapter', mode)
    assert result['outcome'] == 'HARNESS_ERROR', result
    assert result['evidence']['report']['callback_failures']
    assert any(e['event_id'] == 'loop-error' for e in result['evidence']['ledger'])


def test_unowned_setup_threads_are_explicitly_unsupported():
    result = scenario('review_adapter', 'setup_thread')
    assert result['outcome'] == 'UNSUPPORTED', result
    assert 'Thread.start' in result['error']


@pytest.mark.parametrize('options', [['--duration', 'oops'], ['--seed=oops'],
                                    ['--unknown'], ['--duration']])
@pytest.mark.parametrize('output_form', ['separate', 'equals'])
def test_cli_parse_failures_remove_stale_pass(tmp_path, options, output_form):
    output = tmp_path / 'result.json'
    output.write_text(json.dumps({'outcome': 'PASS', 'old': True}))
    target = ['--output', str(output)] if output_form == 'separate' else ['--output=' + str(output)]
    p = subprocess.run([sys.executable, '-m', 'workflow_sim.cli',
                        'workflow_sim.examples.retry:build', *target, *options], capture_output=True)
    assert p.returncode == 4, p.stderr
    assert not output.exists()


@pytest.mark.parametrize('mode', ['retained_error', 'retained_future', 'handled_error',
                                    'handled_future', 'completed_child', 'cancelled_timer'])
def test_async_error_observation_and_descendant_completion(mode):
    result = run('review_adapter:async_ownership', inputs={'mode': mode}, duration=10,
                 project_dir=ADAPTERS)
    broken = mode.startswith('retained_')
    assert result['outcome'] == ('HARNESS_ERROR' if broken else 'PASS'), result
    report = result['evidence']['report']
    assert bool(report['callback_failures']) is broken
    assert report['in_flight'] == []
    for check in result['evidence']['checks']:
        assert check['actual'] == check['expected'], check


@pytest.mark.parametrize('mode', ['thread', 'pool', 'assertion'])
def test_catching_unowned_lifecycle_work_does_not_hide_it(mode):
    result = run('review_adapter:setup_guard', inputs={'mode': mode}, duration=10,
                 project_dir=ADAPTERS)
    assert result['outcome'] == 'UNSUPPORTED', result
    assert result['evidence']['unsupported']
    assert result['evidence']['checks'][0]['actual'] == []


def test_assertion_stage_async_failures_are_in_final_evidence():
    result = run('review_adapter:assertion_error', project_dir=ADAPTERS)
    assert result['outcome'] == 'HARNESS_ERROR', result
    assert 'assertion-stage task error' in str(result['evidence']['report']['callback_failures'])


def test_crash_after_parent_returns_fences_async_descendant():
    result = run('review_adapter:descendant_crash', duration=40, wall_timeout=5, project_dir=ADAPTERS)
    assert result['evidence'] is not None, result
    check = result['evidence']['checks'][0]
    assert check['actual'] == ['parent-returned', True, 'later'], result
    assert result['evidence']['report']['crashes']


@pytest.mark.parametrize('tokens', [['--', '--output'], ['--out'], ['--output-typo']])
def test_cli_parse_errors_do_not_guess_a_different_output_option(tmp_path, tokens):
    output = tmp_path / 'unrelated.json'
    output.write_text('keep me')
    p = subprocess.run([sys.executable, '-m', 'workflow_sim.cli', 'bad-adapter',
                        *tokens, str(output)], capture_output=True)
    assert p.returncode == 4, p.stderr
    assert output.read_text() == 'keep me'


def test_cli_invalid_numeric_before_output_still_invalidates(tmp_path):
    output = tmp_path / 'result.json'
    output.write_text('{"outcome":"PASS"}')
    p = subprocess.run([sys.executable, '-m', 'workflow_sim.cli', 'bad-adapter',
                        '--duration', 'oops', '--output', str(output)], capture_output=True)
    assert p.returncode == 4, p.stderr
    assert not output.exists()


@pytest.mark.parametrize('mode', ['setup', 'raw_pool', 'raw_pool_complete'])
def test_sync_async_bridge_cannot_detach_child_ownership(mode):
    result = run('review_adapter:bridge_descendant', inputs={'mode': mode}, duration=10,
                 wall_timeout=5, project_dir=ADAPTERS)
    assert result['outcome'] == {'setup': 'UNSUPPORTED', 'raw_pool': 'INCOMPLETE', 'raw_pool_complete': 'PASS'}[mode], result
    if mode == 'raw_pool':
        assert result['evidence']['report']['in_flight']
    if mode != 'setup':
        check = result['evidence']['checks'][0]
        assert check['actual'] == check['expected']


def test_coroutine_post_and_worker_block_are_an_atomic_handoff():
    result = run('review_adapter:handoff_stress', duration=83, wall_timeout=15,
                 project_dir=ADAPTERS)
    assert result['outcome'] == 'PASS', result
    check = result['evidence']['checks'][0]
    assert check['actual'] == check['expected']
    assert result['evidence']['report']['in_flight'] == []
