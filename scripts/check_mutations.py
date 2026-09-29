"""Targeted behavioral mutation gate, using a disposable installed source copy.

This is a curated fault campaign, not a whole-project mutation coverage score.
Only assertion failures count as detections; errors and timeouts fail the gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

MUTANTS = [
    ('ignore-descendants', 'engine.py',
     'and (self.loop is None or self.loop_retired)', 'and True',
     'test_adversarial_regressions.py::test_async_descendants_remain_in_flight'),
    ('hide-unretrieved-errors', 'engine.py',
     'if future.done() and getattr(future, "_log_traceback", False):', 'if False:',
     'test_adversarial_regressions.py::test_unhandled_async_failures_veto_matching_checks'),
    ('keep-finished-task-deadlines', 'engine.py',
     'self.cancel_items(lambda item: id(item) in handles)', 'self.cancel_items(lambda item: False)',
     'test_celery_regressions.py::test_success_disarms_task_limits'),
    ('reuse-mutated-message', 'celery_driver.py',
     'args, kwargs = loads(body, content_type, encoding)', 'args, kwargs = run.args, run.kwargs',
     'test_celery_regressions.py::test_duplicate_delivery_has_independent_decoded_payload'),
    ('forget-caught-unsupported', 'celery_driver.py',
     'self.unsupported.append(reason)', 'pass  # silently forget unsupported operation',
     'test_celery_regressions.py::test_unsupported_celery_is_sticky_on_every_publish_path'),
    ('retain-stale-cli-pass', 'cli.py',
     'output = _output_on_parse_error(argv)', 'output = None',
     'test_adversarial_regressions.py::test_cli_parse_failures_remove_stale_pass'),
]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parents[1]
env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
report = {'scope': 'six curated simulator defects; no global mutation score', 'mutants': []}

with tempfile.TemporaryDirectory(prefix='workflow-sim-mutations-') as directory:
    root = Path(directory)
    copy = root / 'source'
    copy.mkdir()
    for name in ('src', 'tests'):
        shutil.copytree(source / name, copy / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('pyproject.toml', 'README.md', 'uv.lock', '.python-version'):
        shutil.copy2(source / name, copy / name)
    python = str(root / 'env/bin/python')
    with (output / 'install.log').open('w') as log:
        subprocess.run(['uv', 'sync', '--locked', '--project', str(copy), '--python', sys.executable],
                       env={**env, 'UV_PROJECT_ENVIRONMENT': str(root / 'env')},
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    # Child runners launch this interpreter themselves: verify its installed import
    # points at the disposable copy, rather than depending on PYTHONPATH leakage.
    installed = subprocess.check_output([python, '-c', 'import workflow_sim; print(workflow_sim.__file__)'],
                                        cwd=root, env=env, text=True).strip()
    assert Path(installed).resolve().is_relative_to(copy.resolve()), installed

    def pytest(name, selections):
        xml = output / (name + '.xml')
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run([python, '-m', 'pytest', '-q', '--junitxml=' + str(xml),
                                     *[str(copy / 'tests' / s) for s in selections]],
                                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        return result.returncode, ET.parse(xml).getroot()

    code, baseline = pytest('baseline', list(dict.fromkeys(m[4] for m in MUTANTS)))
    assert code == 0 and not baseline.findall('.//failure') and not baseline.findall('.//error'), 'baseline failed'
    report['baseline_cases'] = len(baseline.findall('.//testcase'))
    for name, filename, old, new, selection in MUTANTS:
        path = copy / 'src/workflow_sim' / filename
        original = path.read_text()
        assert original.count(old) == 1, (name, 'mutation anchor drift')
        try:
            changed = original.replace(old, new)
            compile(changed, str(path), 'exec')
            path.write_text(changed)
            code, results = pytest(name, [selection])
            failures, errors = results.findall('.//failure'), results.findall('.//error')
            detected = code == 1 and bool(failures) and not errors and all(
                'AssertionError' in (f.text or '') or (f.get('message') or '').startswith('assert ')
                for f in failures)
            report['mutants'].append({'name': name, 'file': filename, 'test': selection,
                                      'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
                                      'replacement': {'old': old, 'new': new},
                                      'detected_by_assertion': detected, 'failed_cases': len(failures)})
            (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
            assert detected, (name, 'survivor or invalid run; inspect logs')
            print(name, 'detected by behavioral assertion', flush=True)
        finally:
            path.write_text(original)
print('All six targeted mutations detected; baseline passed')
