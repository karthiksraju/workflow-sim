"""Adapter import-closure provenance: helper changes move the digest.

The plausible gap each test closes: a behavior-changing helper edit that
leaves the adapter module byte-identical must not keep the old identity.
source_sha256 covers only the adapter module; closure_sha256 covers the
first-party import closure observed at evidence freeze.
"""
import hashlib
import shutil
import sys
import types
from pathlib import Path

from workflow_sim import run
from workflow_sim._worker import adapter_closure

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

ADAPTER_IMPORTS = """
import sys
from pathlib import Path
import helper
sys.path.insert(0, str(Path(__file__).parent / 'fakevenv' / 'site-packages'))
import vendored


def build(ctx):
    seen = []

    def record():
        import lazy
        seen.append([helper.tag(), lazy.tag(), vendored.tag()])

    ctx.at(0, 'record', record)
    ctx.expect('tags', lambda: seen, [['v1', 'lazy1', 'vendored1']])
"""


def write_project(root, helper_src, adapter_src=ADAPTER):
    (root / 'adapter.py').write_text(adapter_src)
    (root / 'helper.py').write_text(helper_src)
    # Bytecode caches key on mtime+size: same-size rapid rewrites could
    # otherwise execute stale bytes while hashing fresh source.
    shutil.rmtree(root / '__pycache__', ignore_errors=True)


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
    # The run really executed v2 bytes, not stale v1 bytecode: the business
    # check observed the v2 tag literally.
    again = run('adapter:build', project_dir=tmp_path, duration=5)
    assert again['outcome'] == 'PASS', again
    assert again['evidence']['checks'][0]['actual'] == ['v2']


def test_closure_excludes_runtime_and_third_party(tmp_path):
    identity = adapter_identity(tmp_path, HELPER_V1)
    tops = {name.split('/')[0].split('.')[0] for name in identity['closure_files']}
    assert tops == {'adapter', 'helper'}
    assert 'workflow_sim' not in tops and 'os' not in tops and 'sys' not in tops


def test_closure_covers_callback_imports_not_vendored(tmp_path):
    (tmp_path / 'fakevenv' / 'site-packages').mkdir(parents=True)
    (tmp_path / 'fakevenv' / 'site-packages' / 'vendored.py').write_text(
        "def tag():\n    return 'vendored1'\n")
    (tmp_path / 'lazy.py').write_text("def tag():\n    return 'lazy1'\n")
    write_project(tmp_path, HELPER_V1, adapter_src=ADAPTER_IMPORTS)
    result = run('adapter:build', project_dir=tmp_path, duration=5)
    assert result['outcome'] == 'PASS', result
    files = result['provenance']['adapter']['closure_files']
    assert set(files) == {'adapter.py', 'helper.py', 'lazy.py'}
    assert not any('site-packages' in name or '.venv' in name for name in files)


def test_installed_example_has_empty_first_party_closure():
    result = run('workflow_sim.examples.retry:build', duration=10)
    assert result['outcome'] == 'PASS', result
    adapter = result['provenance']['adapter']
    assert adapter['closure_files'] == {} and 'source_sha256' in adapter


def test_stdlib_named_modules_excluded_even_under_project(tmp_path):
    """CI installs (uv-managed interpreter under the working dir) resolve the
    real stdlib beneath project_dir; excluding stdlib by module name keeps it
    out regardless of path. Fake modules stand in for that layout."""
    staged = []
    try:
        for name, rel in (('os', 'uvpython/os.py'),
                          ('json', 'uvpython/json.py'),
                          ('antigravity', 'uvpython/antigravity.py'),
                          ('helper', 'helper.py')):
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# stand-in for {name}\n")
            mod = types.ModuleType(name)
            mod.__file__ = str(path)
            staged.append((name, sys.modules.get(name)))
            sys.modules[name] = mod
        closure = adapter_closure(str(tmp_path))
        assert set(closure['closure_files']) == {'helper.py'}
    finally:
        for name, previous in staged:
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous
