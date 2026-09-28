"""Fail closed: release only a tested commit with matching prerelease metadata."""
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

version = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
tag = os.environ['RELEASE_TAG']
assert re.fullmatch(r'v\d+\.\d+\.\d+a\d+', tag) and tag == 'v' + version, 'tag must match alpha metadata'
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert subprocess.check_output(['git', 'rev-parse', tag + '^{commit}'], text=True).strip() == head, 'tag must point to this commit'
runs = json.loads(subprocess.check_output(['gh', 'run', 'list', '--workflow', 'ci.yml', '--commit', head,
                                         '--json', 'conclusion,event,headSha,status', '--limit', '100'], text=True))
assert any(r['conclusion'] == 'success' and r['headSha'] == head and r['event'] in ('push', 'workflow_dispatch') for r in runs), 'both platform CI jobs must pass on the release commit'
assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True), 'checkout must be clean'
