"""Run the live-cert pilot properties and retain fixed/broken evidence.

Sim gates always run through the installed runner: each pilot property must
PASS fixed and ASSERTION_FAILED broken, with the broken run failing at least
one business check and reporting no unsupported paths or violations.

The live prefork gate runs only when LIVE_CERT_BROKER points at a throwaway
Redis (never a production broker): the celery_retry task shape executes under
a real prefork worker and its durable effects must match the sim expectation
when fixed; the mutated-payload variant must show the same damage live.
Without the variable the live gate records an explicit skip, never a pass.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from workflow_sim import run
from workflow_sim.contracts import encode

SIM_CASES = [
    ('celery_retry', 'workflow_sim.examples.celery_retry:build', 10, {'delivered content'}),
    ('billing', 'workflow_sim.examples.billing:build', 15, {'one exact receipt'}),
]


def differs(actual, expected):
    """The runtime compares canonically (true differs from 1); Python == would
    equate False with 0, so the gate must use the same bytes to agree with the
    verdict on which checks failed."""
    return encode(actual) != encode(expected)


def sim_gate(output):
    records = []
    for name, adapter, duration, broken_fails in SIM_CASES:
        for broken in (False, True):
            inputs = {'broken': broken} if broken else {}
            result = run(adapter, inputs=inputs, duration=duration)
            wanted = 'ASSERTION_FAILED' if broken else 'PASS'
            path = output / f'sim-{name}-{"broken" if broken else "fixed"}.json'
            path.write_text(json.dumps(result, indent=2) + '\n')
            assert result['outcome'] == wanted, (name, wanted, result['outcome'])
            checks = result['evidence']['checks']
            failed = {c['name'] for c in checks if differs(c['actual'], c['expected'])}
            # Exact pattern: fixed fails nothing, broken fails only its known check.
            assert failed == (broken_fails if broken else set()) and checks, (name, failed)
            assert not result['evidence']['unsupported'], (name, result['evidence']['unsupported'])
            assert not result['evidence']['violations'], (name, result['evidence']['violations'])
            records.append({'case': name, 'broken': broken, 'outcome': result['outcome'],
                            'failed_checks': sorted(failed),
                            'evidence_sha256': result['evidence_sha256']})
            print(f'sim {name}: {result["outcome"]} ({"broken" if broken else "fixed"})', flush=True)
    return records


def cli_gate(output, tmp):
    """The CLI exit code is part of the contract: 0 PASS, 1 ASSERTION_FAILED."""
    records = []
    name, adapter, duration, _ = SIM_CASES[0]
    entry = str(Path(sys.executable).parent / 'workflow-sim')
    for broken in (False, True):
        inputs = tmp / f'cli-{broken}.json'
        inputs.write_text(json.dumps({'broken': broken}))
        out = tmp / f'cli-{broken}-out.json'
        proc = subprocess.run(
            [entry, f'{adapter}',
             '--inputs', str(inputs), '--duration', str(duration),
             '--output', str(out)], capture_output=True, text=True, timeout=120)
        wanted = 1 if broken else 0
        assert proc.returncode == wanted, (name, wanted, proc.returncode, proc.stderr[-500:])
        result = json.loads(out.read_text())
        assert result['outcome'] == ('ASSERTION_FAILED' if broken else 'PASS'), result['outcome']
        records.append({'case': f'cli-{name}', 'broken': broken,
                        'exit': proc.returncode, 'outcome': result['outcome']})
        print(f'cli {name}: exit {proc.returncode} ({"broken" if broken else "fixed"})', flush=True)
    return records


LIVE_TASKS = """
import fcntl
import json
import os
from pathlib import Path
from celery import Celery
app = Celery('gate', broker=os.environ['LIVE_CERT_BROKER'], backend=os.environ['LIVE_CERT_BROKER'])
app.conf.update(task_serializer='json', result_serializer='json', accept_content=['json'],
                task_acks_late=True, task_reject_on_worker_lost=True,
                worker_prefetch_multiplier=1, task_default_queue=os.environ['LIVE_QUEUE'])
ROOT = Path(os.environ['LIVE_ROOT'])
def record(value):
    with (ROOT / 'effects.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value) + '\\n')
@app.task(bind=True, name='gate.deliver', max_retries=1)
def deliver(self, payload, broken=False):
    record([self.request.retries, dict(payload)])
    if self.request.retries == 0:
        if broken:
            payload['items'].clear()
        raise self.retry(countdown=1)
    return payload
"""


def live_effects(root):
    path = root / 'effects.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def live_gate(output, tmp):
    broker = os.environ.get('LIVE_CERT_BROKER', '')
    if not broker.startswith('redis://'):
        record = {'live': 'skipped', 'reason': 'LIVE_CERT_BROKER not set to a redis:// URL'}
        print('live prefork: skipped (LIVE_CERT_BROKER not set)', flush=True)
        return record
    (tmp / 'gate_tasks.py').write_text(LIVE_TASKS)
    queue = 'livecert-gate-' + uuid.uuid4().hex
    root = tmp / 'live-effects'
    root.mkdir()
    os.environ['LIVE_CERT_BROKER'] = broker
    os.environ['LIVE_QUEUE'] = queue
    os.environ['LIVE_ROOT'] = str(root)
    env = dict(os.environ)
    log = (tmp / 'worker.log').open('w')
    tasks = None
    worker = subprocess.Popen(
        [sys.executable, '-m', 'celery', '-A', 'gate_tasks:app', 'worker',
         '--pool=prefork', '--concurrency=1', '--loglevel=WARNING',
         '--without-gossip', '--without-mingle', '--without-heartbeat'],
        cwd=tmp, stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        sys.path.insert(0, str(tmp))
        import gate_tasks as tasks
        deadline = time.monotonic() + 40
        while not tasks.app.control.ping(timeout=1):
            assert worker.poll() is None, 'worker exited during startup'
            assert time.monotonic() < deadline, 'worker readiness timed out'
        record = {'live': 'checked', 'broker': 'redis'}
        for broken in (False, True):
            (root / 'effects.jsonl').unlink(missing_ok=True)
            payload = {'id': 'order-7', 'items': ['book']}
            tasks.deliver.apply_async(args=(payload, broken), queue=queue).get(timeout=60)
            deadline = time.monotonic() + 15
            while len(live_effects(root)) < 2:
                assert time.monotonic() < deadline, 'missing live durable effects'
                time.sleep(0.2)
            attempts = [a for a, _ in live_effects(root)]
            delivered = [p for _, p in live_effects(root)][-1]
            if broken:
                assert delivered == {'id': 'order-7', 'items': []}, delivered
                assert attempts == [0, 1], attempts
            else:
                assert attempts == [0, 1] and delivered == {'id': 'order-7', 'items': ['book']}, tasks.effects
            record['broken' if broken else 'fixed'] = {'attempts': attempts, 'delivered': delivered}
            print(f'live prefork: {"damage manifests" if broken else "effects match"} '
                  f'({"broken" if broken else "fixed"})', flush=True)
        return record
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=5)
        if tasks is not None:
            tasks.app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        summary = {'sim': sim_gate(args.output),
                   'cli': cli_gate(args.output, tmp),
                   'prefork': live_gate(args.output, tmp)}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('live-cert gate: all required checks held')


if __name__ == '__main__':
    main()
