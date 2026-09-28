"""Private process entrypoint. The parent owns wall time and process cleanup."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .provenance import provenance
from .contracts import SCHEMA_VERSION, MAX_RESULT_BYTES, digest, encode, validate_request, verdict, configuration



def guard(violations):
    def deny(kind):
        def blocked(*args, **kwargs):
            violations.append(kind)
            raise RuntimeError(f'workflow-sim blocks {kind}; replace the external boundary in your adapter')
        return blocked
    # Best-effort guards for trusted adapters, deliberately not called a sandbox.
    for name in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, deny('network'))
    socket.create_connection = deny('network')
    socket.getaddrinfo = deny('DNS')
    subprocess.Popen = deny('subprocess')
    os.system = deny('subprocess')
    for name in ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp'):
        if hasattr(os, name):
            setattr(os, name, deny('subprocess'))


def execute(request, attempt, project, scratch):
    validate_request(request)
    result = {'schema_version': SCHEMA_VERSION, 'attempt': attempt,
              'request_sha256': digest(request), 'outcome': 'HARNESS_ERROR',
              'evidence': None, 'evidence_sha256': None, 'error': None,
              'provenance': {**provenance(), 'configuration': configuration(request)}}
    violations = []
    guard(violations)
    try:
        from .engine import Engine
        from .context import Context
        from .ledger import canonical
        start = datetime(2099, 1, 1, tzinfo=timezone.utc)
        engine = Engine(start=start, seed=request['seed'], max_steps=request['max_steps'], strict_lifecycle=True)
        with engine:
            module_name, function_name = request['adapter'].split(':')
            module = importlib.import_module(module_name)
            source = Path(module.__file__)
            result['provenance']['adapter'] = {'module': module_name, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            context = Context(engine, request['inputs'], Path(project), Path(scratch))
            returned = getattr(module, function_name)(context)
            if returned is not None:
                raise TypeError('adapter must configure the context and return None')
            report = engine.run_until(start + timedelta(seconds=request['duration']))
            checks = context._evaluate()
            # Assertions are adapter code too: incorporate failures or work they
            # create before freezing the evidence (without running it implicitly).
            report = engine._report(report.stop_reason, report.started_at,
                                    engine._steps - report.steps)
        # Include violations from setup, execution, assertions, and teardown.
        evidence = {'report': canonical(asdict(report)), 'checks': checks,
                    'ledger': engine.ledger.records(), 'violations': violations,
                    'unsupported': engine.unsupported}
        result.update(evidence=evidence, evidence_sha256=digest(evidence), outcome=verdict(evidence))
    except BaseException as exc:
        result.update(outcome='UNSUPPORTED' if violations or type(exc).__name__ in ('UnsupportedFeature', 'UnsupportedCeleryFeature', 'ClockRangeError') else 'HARNESS_ERROR',
                      error=f'{type(exc).__name__}: {exc}')
    return result


def main():
    request_path, output_path, attempt, project, scratch = sys.argv[1:]
    request = json.loads(Path(request_path).read_bytes())
    sys.path.insert(0, project)
    os.chdir(scratch)
    result = execute(request, attempt, project, scratch)
    data = encode(result)
    if len(data) > MAX_RESULT_BYTES:
        raise RuntimeError('result exceeds 8 MB')
    temporary = Path(output_path + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(output_path)
    sys.stdout.flush()
    sys.stderr.flush()
    # Abandoned, fenced threads intentionally survive until process exit.
    os._exit(0)


if __name__ == '__main__':
    main()
