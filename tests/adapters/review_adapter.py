import asyncio
import threading
import time
from celery import Celery

def build(c):
    mode = c.inputs['mode']
    state = []
    if mode in ('async_error', 'callback_error', 'future_task', 'future_timer', 'blocked_task', 'awaited_error', 'awaited_future'):
        async def child():
            state.append('child-started')
            c.record('child-started')
            if mode in ('async_error', 'awaited_error'):
                raise RuntimeError('unobserved-child-error')
            if mode == 'blocked_task':
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(100)
            state.append('child-completed')
            c.record('child-completed')
        async def start():
            loop = asyncio.get_running_loop()
            def handler(loop, context):
                c.record('loop-error', message=context['message'], error=repr(context.get('exception')))
                loop.default_exception_handler(context)
            loop.set_exception_handler(handler)
            if mode == 'callback_error':
                def fail():
                    c.record('callback-entered')
                    raise RuntimeError('unobserved-callback-error')
                loop.call_soon(fail)
            elif mode == 'future_timer':
                loop.call_later(100, lambda: c.record('timer-fired'))
            elif mode.startswith('awaited_'):
                await child()
            else:
                asyncio.create_task(child())
                await asyncio.sleep(0)
            state.append('parent-returned')
        c.at(0, 'start', start)
        expected = ['parent-returned'] if mode in ('callback_error','future_timer') else ['child-started','parent-returned']
        c.expect('state at horizon', lambda: state, expected)
    elif mode == 'setup_thread':
        def late():
            time.sleep(2)
            state.append('late-write')
        thread = threading.Thread(target=late, daemon=True)
        thread.start()
        c.record('thread-started')
        c.expect('thread still alive', thread.is_alive, True)
        c.expect('state', lambda: state, [])
    elif mode.startswith('celery_'):
        app = Celery('review', broker='memory://', backend='cache+memory://')
        @app.task(name='review.job')
        def job(value='payload'):
            state.append(value)
            c.record('delivered', value=value)
            return value
        def enqueue():
            if mode == 'celery_caught_unsupported':
                try:
                    job.apply_async(shadow='renamed')
                except RuntimeError as exc:
                    c.record('caught', error=str(exc))
            elif mode == 'celery_limit':
                job.apply_async(time_limit=100, soft_time_limit=50)
            elif mode == 'celery_link_expiry':
                job.apply_async(link=job.si('expired-link').set(countdown=2, expires=1))
            elif mode == 'celery_link_error':
                job.apply_async(link=job.s()) # two args to one-arg task -> task FAILURE (control)
            else:
                job.delay()
        c.at(0, 'enqueue', enqueue)
        expected = [] if mode == 'celery_caught_unsupported' else ['payload','expired-link'] if mode == 'celery_link_expiry' else ['payload']
        c.expect('deliveries', lambda: state, expected)
