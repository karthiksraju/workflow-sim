"""Adapter import-closure provenance: helper changes move the digest.

The plausible gap each test closes: a behavior-changing helper edit that
leaves the adapter module byte-identical must not keep the old identity.
source_sha256 covers only the adapter module; closure_sha256 covers the
first-party import closure observed after the factory ran.
"""
import hashlib
from pathlib import Path

from workflow_sim import run

ADAPTER = """
import helper


def build(ctx):
    seen = []

    def record():
        seen.append(helper.tag())

    ctx.at(0, 'record', record)
    ctx.expect('helper tag', lambda: seen, [helper.tag()])
"""

HELPER_V1 = """
def tag():
    return 'v1'
"""

HELPER_V2 = """
def tag():
    return 'v2'
"""


def write_project(root, helper_src):
    (root / 'adapter.py').write_text(ADAPTER)
    (root / 'helper.py').write_text(helper_src)


def adapter_identity(tmp_path, helper_src):
    write_project(tmp_path, helper_src)
    result = run('adapter:build', project_dir=tmp_path, duration=5)
    assert result['outcome'] == 'PASS', result
    return result['provenance']['adapter']


def test_closure_covers_helper_and_stays_stable(tmp_path):
    first = adapter_identity(tmp_path, HELPER_V1)
    assert first['source_sha256'] == hashlib.sha256((tmp_path / 'adapter.py').read_bytes()).hexdigest()
    assert set(first['closure_files']) == {'adapter.py', 'helper.py'}
    assert first['closure_files']['helper.py'] == hashlib.sha256(HELPER_V1.encode()).hexdigest()
    second = adapter_identity(tmp_path, HELPER_V1)
    assert second['closure_sha256'] == first['closure_sha256']


def test_helper_change_moves_closure_not_source(tmp_path):
    before = adapter_identity(tmp_path, HELPER_V1)
    after = adapter_identity(tmp_path, HELPER_V2)
    assert after['source_sha256'] == before['source_sha256']
    assert after['closure_sha256'] != before['closure_sha256']
    assert after['closure_files']['helper.py'] == hashlib.sha256(HELPER_V2.encode()).hexdigest()


def test_closure_excludes_runtime_and_third_party(tmp_path):
    identity = adapter_identity(tmp_path, HELPER_V1)
    tops = {name.split('/')[0].split('.')[0] for name in identity['closure_files']}
    assert tops == {'adapter', 'helper'}
    assert 'workflow_sim' not in tops and 'os' not in tops and 'sys' not in tops


def test_installed_example_has_empty_first_party_closure():
    result = run('workflow_sim.examples.retry:build', duration=10)
    assert result['outcome'] == 'PASS', result
    adapter = result['provenance']['adapter']
    assert adapter['closure_files'] == {} and 'source_sha256' in adapter
