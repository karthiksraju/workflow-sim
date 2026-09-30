"""Celery countdown retry must preserve its JSON payload and older deliveries.

Synthetic order/store contract; retry serialization uses the real Celery producer.
The broken variant mutates the payload before asking Celery to retry it.
"""
from celery import Celery


def build(ctx):
    app = Celery('example', broker='memory://', backend='cache+memory://')
    attempts = []
    delivered = [{'id': 'older-6', 'items': ['pen']}]
    broken = ctx.inputs.get('broken', False)

    @app.task(bind=True, name='workflow_sim.example.deliver', max_retries=1)
    def deliver(self, payload):
        attempts.append(self.request.retries)
        if self.request.retries == 0:
            if broken:
                payload['items'].clear()  # bug: retry now serializes a damaged order
            raise self.retry(countdown=3)
        delivered.append(payload)

    ctx.at(0, 'enqueue', lambda: deliver.delay({'id': 'order-7', 'items': ['book']}))
    ctx.expect('delivered content', lambda: delivered,
               [{'id': 'older-6', 'items': ['pen']}, {'id': 'order-7', 'items': ['book']}])
    ctx.expect('retry lineage', lambda: attempts, [0, 1])
