"""Check the distribution boundary and run the installed package outside the checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import tarfile

wheel, = Path('dist').glob('*.whl')
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    assert any(n.endswith('workflow_sim/py.typed') for n in names)
    assert any(n.endswith('workflow_sim/examples/retry.py') for n in names)
    assert all(n.startswith(('workflow_sim/', 'workflow_sim-')) for n in names), names
    assert not any('/tests/' in n or '.env' in n or '__pycache__' in n for n in names)
source, = Path('dist').glob('*.tar.gz')
with tarfile.open(source) as archive:
    names = archive.getnames()
    assert any(n.endswith('/docs/architecture.md') for n in names), 'source distribution must include architecture docs'
    assert any(n.endswith('/CONTRIBUTING.md') for n in names)
    lock, = (n for n in names if n.endswith('/requirements-dev.lock'))
    assert archive.extractfile(lock).read() == Path('requirements-dev.lock').read_bytes(), 'source archive contributor lock must match the tested checkout'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    subprocess.run([sys.executable, '-m', 'venv', str(root / 'env')], check=True)
    python = root / 'env/bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', str(wheel.resolve())], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
    for adapter in ('retry', 'celery_retry'):
        p = subprocess.run([str(python), '-m', 'workflow_sim.cli', f'workflow_sim.examples.{adapter}:build', '--duration', '10'], cwd=root, env=env, check=True, capture_output=True, text=True)
        result = json.loads(p.stdout)
        assert result['outcome'] == 'PASS', result
        assert result['evidence']['checks'][0]['actual'] == result['evidence']['checks'][0]['expected']
    (root / 'broken.json').write_text('{"broken":true}')
    p = subprocess.run([str(python), '-m', 'workflow_sim.cli', 'workflow_sim.examples.retry:build', '--duration', '10', '--inputs', 'broken.json'], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)['outcome'] == 'ASSERTION_FAILED'
    subprocess.run([str(python), '-c', "import importlib.util, importlib.metadata, workflow_sim; assert workflow_sim.__version__ == importlib.metadata.version('workflow-sim'); assert importlib.util.find_spec('jobs') is None; assert importlib.util.find_spec('pytest') is None"], cwd=root, env=env, check=True)
print('Clean wheel: independent async and Celery examples PASS; duplicate mutation rejected; no application/test dependencies')
