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
                                         '--json', 'databaseId,conclusion,event,headSha,status', '--limit', '100'], text=True))
successful = [r for r in runs if r['conclusion'] == 'success' and r['headSha'] == head and r['event'] in ('push', 'workflow_dispatch')]
assert successful, 'both platform CI jobs must pass on the release commit'
run_id = successful[0]['databaseId']
jobs = json.loads(subprocess.check_output(['gh', 'run', 'view', str(run_id), '--json', 'jobs'], text=True))['jobs']
assert all(any(j['name'] == name and j['conclusion'] == 'success' for j in jobs) for name in ('test (ubuntu-latest)', 'test (macos-latest)')), 'required platform jobs did not pass'
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write(f'ci_run_id={run_id}\n')
assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True), 'checkout must be clean'
