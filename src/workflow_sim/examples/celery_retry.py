"""Celery producer/task path with a countdown retry and JSON serialization."""
from celery import Celery


def build(ctx):
    app = Celery('example', broker='memory://', backend='cache+memory://')
    attempts = []
    delivered = []

    @app.task(bind=True, name='workflow_sim.example.deliver', max_retries=1)
    def deliver(self, payload):
        attempts.append(self.request.retries)
        if self.request.retries == 0:
            raise self.retry(countdown=3)
        delivered.append(payload)

    ctx.at(0, 'enqueue', lambda: deliver.delay({'id': 'order-7', 'items': ['book']}))
    ctx.expect('delivered content', lambda: delivered, [{'id': 'order-7', 'items': ['book']}])
    ctx.expect('retry lineage', lambda: attempts, [0, 1])
