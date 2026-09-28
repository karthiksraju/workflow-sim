from celery import Celery, chain
from workflow_sim import runtime
import asyncio

def build(c):
    mode = c.inputs['mode']
    state=[]
    app=Celery('review2', broker='memory://', backend='cache+memory://')
    @app.task(bind=True, name='review2.first')
    def first(self, payload='payload'):
        state.append(['first',self.request.retries,payload])
        if self.request.retries == 0 and 'retry' in mode:
            raise self.retry(countdown=1)
        return payload
    @app.task(name='review2.last')
    def last(payload):
        state.append(['last',payload])
        return payload
    def start():
        if mode == 'retry_link':
            first.apply_async(link=last.s())
        elif mode == 'retry_chain':
            chain(first.s(),last.s()).apply_async()
        elif mode == 'plain_chain':
            chain(first.s(),last.s()).apply_async()
        elif mode == 'direct_expiry':
            last.apply_async(args=['expired'], countdown=2, expires=1)
        elif mode == 'link_expiry':
            first.apply_async(link=last.si('expired').set(countdown=2,expires=1))
        elif mode == 'link_unsupported':
            first.apply_async(link=last.s().set(shadow='renamed'))
        elif mode == 'broken_link_payload':
            # Parent succeeds, but serialization of its return value for the next task fails.
            @app.task(name='review2.bad_result')
            def bad_result():
                state.append(['first-completed'])
                return object()
            bad_result.apply_async(link=last.s())
    c.at(0,'enqueue',start)
    expected = [['first',0,'payload'],['first',1,'payload'],['last','payload']] if 'retry' in mode else [] if mode=='direct_expiry' else [['first-completed']] if mode=='broken_link_payload' else [['first',0,'payload']] if mode=='link_expiry' else [['first',0,'payload'],['last','payload']]
    c.expect('complete workflow',lambda:state,expected)


def composed(c):
    """Retry metadata comes from Celery's actual request/signature producer."""
    mode = c.inputs['mode']
    app = Celery('composed', broker='memory://', backend='cache+memory://')
    state, attempts, ids = [], [], []

    @app.task(bind=True, name='composed.first', max_retries=1)
    def first(self, payload):
        attempts.append(self.request.retries)
        ids.append(self.request.id)
        if self.request.retries == 0:
            raise self.retry(countdown=1, exc=ValueError('retry'))
        if mode == 'terminal_error':
            raise ValueError('terminal')
        return payload

    @app.task(name='composed.append')
    def append(payload, step):
        state.append({'step': step, 'payload': payload})
        return payload

    @app.task(name='composed.errback')
    def errback(task_id):
        state.append({'step': 'error', 'payload': task_id})

    def start():
        if mode == 'three_stage':
            chain(first.s({'id': '42'}), append.s('second'), append.s('third')).apply_async()
        else:
            first.apply_async(args=[{'id': '42'}], task_id='fixed-id',
                              link=append.s('success'), link_error=errback.s(),
                              soft_time_limit=50, time_limit=100)
    c.at(0, 'start', start)
    expected = [{'step': 'error', 'payload': 'fixed-id'}] if mode == 'terminal_error' else [
        {'step': step, 'payload': {'id': '42'}} for step in (
            ['second', 'third'] if mode == 'three_stage' else ['success'])]
    c.expect('complete workflow', lambda: state, expected)
    c.expect('attempts', lambda: attempts, [0, 1])
    c.expect('retry keeps identity', lambda: len(set(ids)), 1)


def duplicate(c):
    app = Celery('duplicate', broker='memory://', backend='cache+memory://')
    state = []

    @app.task(name='duplicate.mutate')
    def mutate(payload):
        state.append(list(payload))
        payload.append('worker-mutation')

    def start():
        mutate.delay(['original'])
        c.engine.celery.duplicate(c.engine.celery.pending()[0], delay=1)
    c.at(0, 'start', start)
    c.expect('each delivery decodes the original message', lambda: state,
             [['original'], ['original']])


def completion_hook(c):
    app = Celery('hook', broker='memory://', backend='cache+memory://')
    state = []

    @app.task(name='hook.first')
    def first():
        state.append('delivered')

    def broken(run):
        raise RuntimeError('completion hook broke')
    c.engine.celery.on_run_end = broken
    c.at(0, 'start', lambda: first.delay())
    c.expect('task body succeeded', lambda: state, ['delivered'])


def link_identity(c):
    app = Celery('link_identity', broker='memory://', backend='cache+memory://')
    state = []

    @app.task(name='link_identity.first')
    def first():
        return {'id': 'payload-42'}

    @app.task(bind=True, name='link_identity.last')
    def last(self, payload):
        state.append({'task_id': self.request.id, 'priority': self.request.delivery_info['priority'],
                      'payload': payload})

    c.at(0, 'start', lambda: first.apply_async(link=last.s().set(task_id='frozen-child-id', priority=7)))
    c.expect('signature identity and options survive publication', lambda: state,
             [{'task_id': 'frozen-child-id', 'priority': 7, 'payload': {'id': 'payload-42'}}])
