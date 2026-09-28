"""Shared task bodies for Redis/prefork and workflow-sim contract checks.

Payloads follow Celery's documented JSON task protocol. Files stand in for an
external durable effect; no provider or database behavior is inferred from them.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path

from celery import Celery, chain

app = Celery('contracts', broker=os.environ.get('CONTRACT_BROKER', 'memory://'),
             backend=os.environ.get('CONTRACT_BROKER', 'cache+memory://'))
app.conf.update(task_serializer='json', result_serializer='json', accept_content=['json'],
                task_acks_late=True, task_reject_on_worker_lost=True,
                worker_prefetch_multiplier=1, worker_lost_wait=1,
                task_default_queue=os.environ.get('CONTRACT_QUEUE', 'contracts'))


def record(root, value):
    with (Path(root) / 'effects.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def records(root):
    path = Path(root) / 'effects.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@app.task(bind=True, name='contracts.retry', max_retries=1)
def retry(self, root, payload):
    record(root, ['attempt', self.request.retries, self.request.id, payload])
    if not self.request.retries:
        raise self.retry(countdown=1)
    return payload


@app.task(name='contracts.finish')
def finish(payload, root):
    record(root, ['finished', payload])
    return payload


@app.task(bind=True, name='contracts.mutate')
def mutate(self, root, payload, crash=False):
    record(root, ['received', bool(self.request.delivery_info.get('redelivered')),
                  list(payload), self.request.id])
    payload.append('changed in worker')
    marker = Path(root) / 'first-attempt'
    if crash and not marker.exists():
        marker.write_text('effect committed before acknowledgement')
        if os.environ.get('CONTRACT_MODE') == 'simulation':
            from workflow_sim.runtime import run_coro_sync
            run_coro_sync(asyncio.sleep(100))
        else:
            os._exit(73)  # actual prefork child loss, parent and broker survive
    return payload


def publish(case, root):
    if case == 'retry-chain':
        return chain(retry.s(root, {'items': ['book']}).set(task_id='retry-id'),
                     finish.s(root)).apply_async()
    if case == 'duplicate-json':
        mutate.apply_async(args=(root, ['original']), task_id='duplicate-id')
        return mutate.apply_async(args=(root, ['original']), task_id='duplicate-id')
    if case == 'worker-loss':
        return mutate.apply_async(args=(root, ['original'], True), task_id='crash-id')
    if case == 'expiry':
        return finish.apply_async(args=('must not be written', root), countdown=2, expires=1)
    raise ValueError(case)


EXPECTED = {
    'retry-chain': [['attempt', 0, 'retry-id', {'items': ['book']}],
                    ['attempt', 1, 'retry-id', {'items': ['book']}],
                    ['finished', {'items': ['book']}]],
    'duplicate-json': [['received', False, ['original'], 'duplicate-id']] * 2,
    'worker-loss': [['received', False, ['original'], 'crash-id'],
                    ['received', True, ['original'], 'crash-id']],
    'expiry': [],
}


def build(ctx):
    os.environ['CONTRACT_MODE'] = 'simulation'
    case, root = ctx.inputs['case'], ctx.inputs['root']
    ctx.at(0, 'publish', lambda: publish(case, root))
    if case == 'worker-loss':
        def lose_worker():
            work = next(w for w in ctx.engine.executions if w.run is not None)
            work.run.redeliver = True
            ctx.engine.crash_execution(work)
        ctx.at(1, 'lose worker after durable effect', lose_worker)
    ctx.expect('durable effects', lambda: records(root), EXPECTED[case])
