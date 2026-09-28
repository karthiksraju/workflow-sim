"""Functional alpha API tests. Each adapter executes in a real child process.

Fixtures are synthetic workflow/boundary implementations defined here, not
mocked internal runtime functions. Assertions inspect artifacts and final state.
"""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from workflow_sim import run
from workflow_sim.contracts import digest, validate_result


def adapter(tmp_path, source, **kwargs):
    (tmp_path / 'probe.py').write_text(source)
    return run('probe:build', project_dir=tmp_path, **kwargs)


def test_retry_content_and_deliberate_duplicate_mutation():
    good = run('workflow_sim.examples.retry:build', duration=10, seed=41)
    broken = run('workflow_sim.examples.retry:build', inputs={'broken': True}, duration=10, seed=41)
    assert good['outcome'] == 'PASS', good
    assert broken['outcome'] == 'ASSERTION_FAILED', broken
    check = broken['evidence']['checks'][0]
    assert check['actual'] == check['expected'] * 2
    assert [e['data'] for e in good['evidence']['ledger'] if e['event_id'] == 'delivery_committed'] == [{'id': 'invoice-42', 'amount': 1200}]
    again = run('workflow_sim.examples.retry:build', duration=10, seed=41)
    assert good['evidence_sha256'] == again['evidence_sha256']
    assert good['attempt'] != again['attempt']


def test_real_celery_retry_and_delivered_json():
    result = run('workflow_sim.examples.celery_retry:build', duration=10)
    assert result['outcome'] == 'PASS', result
    assert result['evidence']['checks'][0]['actual'] == [{'id': 'order-7', 'items': ['book']}]
    assert result['evidence']['checks'][1]['actual'] == [0, 1]


def test_caller_clock_uuid_and_asyncio_untouched(tmp_path):
    import uuid
    before = (asyncio.BaseEventLoop.time, uuid.uuid4, time.time)
    result = adapter(tmp_path, 'def build(c):\n c.expect("ok", lambda: 1, 1)\n')
    assert result['outcome'] == 'PASS'
    assert before == (asyncio.BaseEventLoop.time, uuid.uuid4, time.time)
    assert not any(name.startswith(('jobs.', 'utils.', 'model.', 'services.')) for name in sys.modules)


@pytest.mark.parametrize('stage', ['factory', 'callback', 'assertion'])
def test_wall_timeout_covers_every_adapter_stage(tmp_path, stage):
    body = {'factory': 'spin()', 'callback': 'c.at(0, "spin", spin)',
            'assertion': 'c.expect("spinning check", spin, 1)'}[stage]
    result = adapter(tmp_path, 'def spin():\n while True: pass\ndef build(c):\n ' + body + '\n', wall_timeout=1)
    assert result['outcome'] == 'INCOMPLETE'
    assert result['error'] == 'wall time budget exhausted'


def test_future_work_cannot_pass_with_trivial_check(tmp_path):
    result = adapter(tmp_path, 'def build(c):\n c.at(100, "future", lambda: None)\n c.expect("trivial", lambda: 1, 1)\n', duration=10)
    assert result['outcome'] == 'INCOMPLETE'
    assert result['evidence']['report']['pending_items'][0]['label'] == 'future'


def test_no_checks_cannot_pass(tmp_path):
    assert adapter(tmp_path, 'def build(c): pass\n')['outcome'] == 'INCOMPLETE'


def test_swallowed_network_attempt_still_prevents_pass(tmp_path):
    result = adapter(tmp_path, '''import socket
def build(c):
 try: socket.create_connection(('127.0.0.1', 1))
 except RuntimeError: pass
 c.expect('otherwise green', lambda: 1, 1)
''')
    assert result['outcome'] == 'UNSUPPORTED'
    assert result['evidence']['violations'] == ['network']


def test_callback_exception_prevents_green_checks_from_passing(tmp_path):
    result = adapter(tmp_path, '''def build(c):
 c.at(0, 'bad callback', lambda: 1/0)
 c.expect('otherwise green', lambda: 1, 1)
''')
    assert result['outcome'] == 'HARNESS_ERROR'
    assert 'ZeroDivisionError' in result['evidence']['report']['callback_failures'][0]['error']


def test_expected_values_snapshot_and_bool_is_not_integer(tmp_path):
    result = adapter(tmp_path, '''def build(c):
 expected = {'items': [1]}
 c.expect('snapshot', lambda: {'items': [1]}, expected)
 expected['items'].append(2)
 c.expect('typed JSON', lambda: True, 1)
''')
    assert result['outcome'] == 'ASSERTION_FAILED'
    assert result['evidence']['checks'][0]['expected'] == {'items': [1]}


@pytest.mark.parametrize('kwargs', [{'seed': True}, {'duration': float('nan')}, {'duration': -1},
                                    {'inputs': {1: 'coerced'}}, {'inputs': []}, {'max_steps': 0},
                                    {'wall_timeout': True}])
def test_invalid_requests_rejected_before_execution(kwargs):
    with pytest.raises(ValueError):
        run('workflow_sim.examples.retry:build', **kwargs)


def test_result_validation_rejects_stale_tampered_and_forged_pass():
    request = {'schema_version': 1, 'adapter': 'workflow_sim.examples.retry:build', 'inputs': {},
               'duration': 10, 'seed': 0, 'max_steps': 100000}
    result = run(request['adapter'], duration=10)
    for mutation in ('attempt', 'hash', 'false_verdict', 'no_checks'):
        edited = copy.deepcopy(result)
        if mutation == 'attempt': edited['attempt'] = 'old-attempt'
        if mutation == 'hash': edited['evidence']['checks'][0]['actual'] = []
        if mutation == 'false_verdict':
            edited['evidence']['checks'][0]['actual'] = []
            edited['evidence_sha256'] = digest(edited['evidence'])
        if mutation == 'no_checks':
            edited['evidence']['checks'] = []
            edited['evidence_sha256'] = digest(edited['evidence'])
        with pytest.raises(ValueError):
            validate_result(edited, request, result['attempt'])


def test_hash_seed_is_stable_across_processes(tmp_path):
    source = '''def build(c):
 c.record('iteration', values=list({'a', 'b', 'c', 'd', 'e', 'f'}))
 c.expect('ok', lambda: 1, 1)
'''
    a, b = [adapter(tmp_path, source, seed=41) for _ in range(2)]
    assert a['outcome'] == b['outcome'] == 'PASS'
    assert a['evidence_sha256'] == b['evidence_sha256']


def test_cli_failure_replaces_existing_pass_artifact(tmp_path):
    output = tmp_path / 'result.json'
    output.write_text('{"outcome":"PASS"}')
    inputs = tmp_path / 'inputs.json'
    inputs.write_text('{"broken":true}')
    process = subprocess.run([sys.executable, '-m', 'workflow_sim.cli', 'workflow_sim.examples.retry:build',
                              '--duration', '10', '--inputs', str(inputs), '--output', str(output)], capture_output=True)
    assert process.returncode == 1
    result = json.loads(output.read_text())
    assert result['outcome'] == 'ASSERTION_FAILED'
    assert len(result['evidence']['checks'][0]['actual']) == 2


def test_output_flood_is_bounded_and_next_run_works(tmp_path):
    result = adapter(tmp_path, 'def build(c):\n while True: print("x" * 65536)\n', wall_timeout=5)
    assert result['outcome'] == 'INCOMPLETE'
    assert result['error'] == 'worker output budget exhausted'
    assert run('workflow_sim.examples.retry:build', duration=10)['outcome'] == 'PASS'


def test_cli_invalid_input_removes_previous_pass(tmp_path):
    output = tmp_path / 'result.json'
    output.write_text('{"outcome":"PASS"}')
    p = subprocess.run([sys.executable, '-m', 'workflow_sim.cli', 'bad-format', '--output', str(output)], capture_output=True)
    assert p.returncode == 4
    assert not output.exists()


def test_monitoring_slot_conflict_does_not_replace_other_tool(tmp_path):
    code = '''import sys
from datetime import datetime, timezone
from workflow_sim.clock import _FENCE_TOOL, fence
sys.monitoring.use_tool_id(_FENCE_TOOL, 'another-tool')
try:
 fence(object(), [])
except RuntimeError as exc:
 assert 'occupied' in str(exc)
else:
 raise AssertionError('silently replaced another monitoring tool')
assert sys.monitoring.get_tool(_FENCE_TOOL) == 'another-tool'
'''
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr


def test_subprocess_attempt_is_explicitly_unsupported(tmp_path):
    result = adapter(tmp_path, '''import subprocess
def build(c):
 subprocess.run(['true'])
 c.expect('ok', lambda: 1, 1)
''')
    assert result['outcome'] == 'UNSUPPORTED'
    assert 'blocks subprocess' in result['error']


def test_timeout_reaps_worker_before_delayed_filesystem_effect(tmp_path):
    marker = tmp_path / 'must-not-exist'
    pidfile = tmp_path / 'worker-pid'
    result = adapter(tmp_path, f'''import asyncio, os, time
from pathlib import Path
def build(c):
 Path({str(pidfile)!r}).write_text(str(os.getpid()))
 def delayed():
  time.sleep(2)
  Path({str(marker)!r}).write_text('escaped worker')
 async def task():
  await asyncio.to_thread(delayed)
 c.at(0, 'delayed effect', task)
 c.expect('ok', lambda: 1, 1)
''', wall_timeout=1)
    assert result['outcome'] == 'INCOMPLETE'
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
    time.sleep(1.2)
    assert not marker.exists()
    assert result['provenance']['configuration']['seed'] == 0


def test_result_configuration_and_error_keep_their_json_types():
    request = {'schema_version': 1, 'adapter': 'workflow_sim.examples.retry:build', 'inputs': {},
               'duration': 10, 'seed': 1, 'max_steps': 100000}
    result = run(request['adapter'], duration=10, seed=1)
    wrong_seed = copy.deepcopy(result)
    wrong_seed['provenance']['configuration']['seed'] = True  # Python equality alone accepts True == 1
    with pytest.raises(ValueError, match='configuration'):
        validate_result(wrong_seed, request, result['attempt'])
    wrong_error = copy.deepcopy(result)
    wrong_error.update(outcome='HARNESS_ERROR', evidence=None, evidence_sha256=None, error={'message': 'bad'})
    with pytest.raises(ValueError):
        validate_result(wrong_error, request, result['attempt'])
