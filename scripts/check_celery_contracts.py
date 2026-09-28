"""Compare installed simulator with actual Linux prefork Celery and private Redis.

Set CONTRACT_BROKER to a disposable Redis URL. Never use a production broker.
This script creates a unique queue; it does not flush any Redis database.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import workflow_sim
from workflow_sim import run
import redis

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
assert sys.platform == 'linux', 'prefork reference runs require Linux'
assert os.environ.get('CONTRACT_BROKER', '').startswith('redis://'), 'disposable Redis URL required'
source = Path(__file__).resolve().parents[1] / 'confidence'
sys.path.insert(0, str(source))
os.environ['CONTRACT_QUEUE'] = 'workflow-sim-contract-' + uuid.uuid4().hex
import celery_contracts as contracts

args.output.mkdir(parents=True, exist_ok=True)
report = {'versions': {p: importlib.metadata.version(p) for p in ('workflow-sim', 'celery', 'redis')},
          'task_source_sha256': hashlib.sha256((source / 'celery_contracts.py').read_bytes()).hexdigest(),
          'python': sys.version, 'pool': 'prefork', 'broker': 'Redis',
          'redis_server': redis.Redis.from_url(os.environ['CONTRACT_BROKER']).info('server')['redis_version'],
          'runtime_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in Path(workflow_sim.__file__).parent.glob('*.py')},
          'cases': []}
with tempfile.TemporaryDirectory() as directory, (args.output / 'worker.log').open('w') as log:
    root = Path(directory)
    worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'celery_contracts:app',
                               'worker', '--pool=prefork', '--concurrency=1', '--loglevel=INFO',
                               '--without-gossip', '--without-mingle', '--without-heartbeat'],
                              cwd=source, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 40
        while not contracts.app.control.ping(timeout=1):
            assert worker.poll() is None, 'worker exited during startup'
            assert time.monotonic() < deadline, 'worker readiness timed out'
        for case, expected in contracts.EXPECTED.items():
            real, simulated = root / (case + '-real'), root / (case + '-sim')
            real.mkdir(); simulated.mkdir()
            result = contracts.publish(case, str(real))
            if case == 'expiry':
                deadline = time.monotonic() + 20
                while result.state != 'REVOKED':
                    assert time.monotonic() < deadline, 'expired task not revoked'
                    time.sleep(.1)
            else:
                result.get(timeout=40)
                deadline = time.monotonic() + 10
                while len(contracts.records(real)) < len(expected):
                    assert time.monotonic() < deadline, 'missing durable effects'
                    time.sleep(.1)
            actual = contracts.records(real)
            assert actual == expected, (case, actual, expected)
            simulation = run('celery_contracts:build', inputs={'case': case, 'root': str(simulated)},
                             duration=10, wall_timeout=15, project_dir=source)
            (args.output / (case + '.json')).write_text(json.dumps(simulation, indent=2))
            assert simulation['outcome'] == ('HARNESS_ERROR' if case == 'worker-loss' else 'PASS'), simulation
            assert contracts.records(simulated) == actual, (case, simulation)
            assert all(c['actual'] == c['expected'] for c in simulation['evidence']['checks']), simulation
            if case == 'expiry':
                assert any(e['kind'] == 'task' and e['data']['state'] == 'REVOKED'
                           for e in simulation['evidence']['ledger']), simulation
            report['cases'].append({'case': case, 'real_state': result.state,
                                    'simulated_outcome': simulation['outcome'], 'effects': actual})
            print(case, 'matches actual prefork worker', flush=True)
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill(); worker.wait(timeout=5)
        contracts.app.close()
(args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
