"""Public process supervisor; importing this module does not patch the caller."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from .contracts import (SCHEMA_VERSION, MAX_OUTPUT_BYTES, MAX_RESULT_BYTES,
                        digest, encode, validate_request, validate_result)


def run(adapter: str, *, inputs: dict | None = None, duration: float = 60,
        seed: int = 0, max_steps: int = 100_000, wall_timeout: float = 30,
        project_dir: str | Path | None = None) -> dict:
    """Run one trusted adapter in a fresh process and return a JSON result.

    Invalid caller configuration raises ValueError. Execution failures return an
    explicit non-PASS outcome. See docs/contracts.md for coverage and limits.
    """
    if platform.python_implementation() != 'CPython' or sys.version_info[:2] != (3, 12) or sys.platform not in ('linux', 'darwin'):
        raise RuntimeError('alpha supports CPython 3.12 on Linux and macOS')
    request = {'schema_version': SCHEMA_VERSION, 'adapter': adapter, 'inputs': {} if inputs is None else inputs,
               'duration': duration, 'seed': seed, 'max_steps': max_steps}
    validate_request(request)
    if type(wall_timeout) not in (int, float) or not math.isfinite(wall_timeout) or not 0 < wall_timeout <= 3600:
        raise ValueError('wall_timeout must be finite seconds in (0, 3600]')
    project = Path(project_dir or Path.cwd()).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('project_dir must be a directory')
    attempt = str(uuid.uuid4())
    base = {'schema_version': SCHEMA_VERSION, 'attempt': attempt, 'request_sha256': digest(request),
            'outcome': 'HARNESS_ERROR', 'evidence': None, 'evidence_sha256': None,
            'provenance': {}, 'error': None}
    with tempfile.TemporaryDirectory(prefix='workflow-sim-') as directory:
        root = Path(directory)
        (root / 'request.json').write_bytes(encode(request))
        (root / 'scratch').mkdir()
        command = [sys.executable, '-s', '-P', '-m', 'workflow_sim._worker', str(root / 'request.json'),
                   str(root / 'result.json'), attempt, str(project), str(root / 'scratch')]
        # Ignore caller Python overrides while fixing hash iteration order per seed.
        env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
        env['PYTHONHASHSEED'] = str(seed % 2**32)
        proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        output = bytearray()
        failure = None
        deadline = time.monotonic() + wall_timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        failure = ('INCOMPLETE', 'wall time budget exhausted')
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk[:MAX_OUTPUT_BYTES + 1 - len(output)])
                    if len(output) > MAX_OUTPUT_BYTES:
                        failure = ('INCOMPLETE', 'worker output budget exhausted')
                        break
                    if proc.poll() is not None and not selector.get_map():
                        break
        finally:
            # Reap owned descendants even on success or caller interruption.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
        if failure:
            base.update(outcome=failure[0], error=failure[1])
            return base
        if proc.returncode != 0:
            base['error'] = f'worker exited with code {proc.returncode}: ' + output.decode('utf-8', errors='replace')[-4000:]
            return base
        try:
            path = root / 'result.json'
            if path.stat().st_size > MAX_RESULT_BYTES:
                raise ValueError('result exceeds 8 MB')
            result = json.loads(path.read_bytes())
            return validate_result(result, request, attempt)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            base['error'] = f'invalid worker result: {type(exc).__name__}: {exc}'
            return base
