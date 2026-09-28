import asyncio
from celery import Celery
from workflow_sim import runtime

def build(c):
    app=Celery('crashreview',broker='memory://',backend='cache+memory://')
    received=[]
    marker=[]
    @app.task(bind=True,name='crashreview.job')
    def job(self,payload):
        received.append({'redelivered':self.request.delivery_info['redelivered'],'payload':list(payload)})
        payload.append('mutated-in-worker')
        if not self.request.delivery_info['redelivered']:
            async def wait():
                await asyncio.sleep(100)
                marker.append('escaped')
            runtime.run_coro_sync(wait())
    c.at(0,'enqueue',lambda:job.delay(['original']))
    def crash():
        work=next(w for w in c.engine.executions if w.run is not None)
        work.run.redeliver=True
        c.engine.crash_execution(work)
    c.at(1,'crash',crash)
    c.expect('redelivery preserves broker payload',lambda:received,[{'redelivered':False,'payload':['original']},{'redelivered':True,'payload':['original']}])
    c.expect('fence prevents continuation',lambda:marker,[])
