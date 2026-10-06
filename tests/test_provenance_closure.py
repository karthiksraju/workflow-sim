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


def test_stdlib_roots_excluded_by_location(tmp_path):
    """A uv-managed interpreter beneath project_dir resolves the real stdlib
    there (the CI failure: 192 stdlib files attributed). Exclusion is by
    interpreter root location, so it holds regardless of path. Mocked roots
    stand in for that layout."""
    import sysconfig
    from unittest import mock
    lib = tmp_path / 'uvpython' / 'lib' / 'python3.12'
    lib.mkdir(parents=True)
    for name in ('os', 'json', '_sysconfigdata__linux_x86_64-linux-gnu'):
        (lib / f'{name}.py').write_text(f"# stand-in for stdlib {name}\n")
    (tmp_path / 'helper.py').write_text("def tag():\n    return 'v1'\n")
    real_get_path = sysconfig.get_path
    staged = []
    try:
        for name in ('os', 'json', '_sysconfigdata__linux_x86_64-linux-gnu', 'helper'):
            filename = f'{name}.py' if name == 'helper' else f'uvpython/lib/python3.12/{name}.py'
            mod = types.ModuleType(name)
            mod.__file__ = str(tmp_path / filename)
            staged.append((name, sys.modules.get(name)))
            sys.modules[name] = mod
        with mock.patch.object(sysconfig, 'get_path', side_effect=(
                lambda kind: str(lib) if kind in ('stdlib', 'platstdlib')
                else real_get_path(kind))):
            closure = adapter_closure(str(tmp_path))
        assert set(closure['closure_files']) == {'helper.py'}
    finally:
        for name, previous in staged:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_first_party_shadow_of_stdlib_name_stays_attributed(tmp_path):
    """A first-party file that shadows a stdlib name changes behavior and must
    move the digest: location (outside the interpreter roots), not the name,
    decides attribution."""
    (tmp_path / 'antigravity.py').write_text("def tag():\n    return 'v1'\n")
    staged = (sys.modules.get('antigravity'),)
    try:
        mod = types.ModuleType('antigravity')
        mod.__file__ = str(tmp_path / 'antigravity.py')
        sys.modules['antigravity'] = mod
        closure = adapter_closure(str(tmp_path))
        assert closure['closure_files'] == {
            'antigravity.py': hashlib.sha256((tmp_path / 'antigravity.py').read_bytes()).hexdigest()}
        (tmp_path / 'antigravity.py').write_text("def tag():\n    return 'v2-longer'\n")
        again = adapter_closure(str(tmp_path))
        assert again['closure_sha256'] != closure['closure_sha256']
    finally:
        if staged[0] is None:
            sys.modules.pop('antigravity', None)
        else:
            sys.modules['antigravity'] = staged[0]
