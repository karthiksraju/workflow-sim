"""Versioned JSON contracts shared by the supervisor and worker."""
from __future__ import annotations

import hashlib
import json
import math
import re

SCHEMA_VERSION = 1
OUTCOMES = {'PASS', 'ASSERTION_FAILED', 'INCOMPLETE', 'UNSUPPORTED', 'HARNESS_ERROR'}
MAX_REQUEST_BYTES = 1_000_000
MAX_RESULT_BYTES = 8_000_000
MAX_OUTPUT_BYTES = 256_000


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def json_value(value):
    """Reject implicit JSON coercion (tuple, integer keys, NaN, custom objects)."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            json_value(item)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            json_value(item)
        return
    raise ValueError('expected finite JSON values with string object keys')


def validate_request(request):
    if set(request) != {'schema_version', 'adapter', 'inputs', 'duration', 'seed', 'max_steps'}:
        raise ValueError('unknown or missing request fields')
    if type(request['schema_version']) is not int or request['schema_version'] != SCHEMA_VERSION:
        raise ValueError('unsupported request schema')
    if not isinstance(request['adapter'], str) or not re.fullmatch(
            r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', request['adapter']):
        raise ValueError('adapter must be module:function')
    if type(request['inputs']) is not dict:
        raise ValueError('inputs must be a JSON object')
    json_value(request['inputs'])
    if type(request['seed']) is not int or not 0 <= request['seed'] < 2**64:
        raise ValueError('seed must be an unsigned 64-bit integer')
    if type(request['max_steps']) is not int or not 1 <= request['max_steps'] <= 1_000_000:
        raise ValueError('max_steps must be between 1 and 1000000')
    if type(request['duration']) not in (int, float) or not math.isfinite(request['duration']) or not 0 <= request['duration'] <= 86400 * 366:
        raise ValueError('duration must be finite seconds between 0 and 366 days')
    if len(encode(request)) > MAX_REQUEST_BYTES:
        raise ValueError('request exceeds 1 MB')


def verdict(evidence):
    if evidence['violations'] or evidence['unsupported']:
        return 'UNSUPPORTED'
    report = evidence['report']
    if (report['stop_reason'] != 'horizon' or any(report[k] for k in (
            'in_flight', 'pending_tasks', 'pending_items', 'dropped_timers'))):
        return 'INCOMPLETE'
    if report['callback_failures'] or report['task_failures']:
        return 'HARNESS_ERROR'
    if not evidence['checks']:
        return 'INCOMPLETE'
    if any(encode(c['actual']) != encode(c['expected']) for c in evidence['checks']):
        return 'ASSERTION_FAILED'
    return 'PASS'


def validate_result(result, request, attempt):
    """Verify the child envelope and recompute its verdict, never trust PASS alone."""
    if not isinstance(result, dict) or set(result) != {
            'schema_version', 'attempt', 'request_sha256', 'outcome', 'evidence',
            'evidence_sha256', 'provenance', 'error'}:
        raise ValueError('malformed result envelope')
    if type(result['schema_version']) is not int or result['schema_version'] != SCHEMA_VERSION:
        raise ValueError('unsupported result schema')
    if result['attempt'] != attempt or result['request_sha256'] != digest(request):
        raise ValueError('result belongs to another run')
    if result['outcome'] not in OUTCOMES:
        raise ValueError('unknown outcome')
    json_value(result)
    if result['evidence'] is not None:
        evidence = result['evidence']
        if set(evidence) != {'report', 'checks', 'ledger', 'violations', 'unsupported'}:
            raise ValueError('invalid evidence')
        if not all(type(evidence[k]) is list for k in ('checks', 'ledger', 'violations', 'unsupported')):
            raise ValueError('invalid evidence collections')
        names = set()
        for check in evidence['checks']:
            if set(check) != {'name', 'actual', 'expected'} or not isinstance(check['name'], str) or not check['name'] or check['name'] in names:
                raise ValueError('invalid or duplicate check')
            names.add(check['name'])
        if digest(evidence) != result['evidence_sha256']:
            raise ValueError('evidence digest mismatch')
        if result['outcome'] != verdict(evidence):
            raise ValueError('verdict contradicts evidence')
        if result['error'] is not None:
            raise ValueError('completed result contains an error')
    elif result['outcome'] == 'PASS' or result['evidence_sha256'] is not None or not result['error']:
        raise ValueError('result has no evidence')
    if not isinstance(result['provenance'], dict) or not result['provenance'].get('library_sha256'):
        raise ValueError('missing runtime provenance')
    return result
