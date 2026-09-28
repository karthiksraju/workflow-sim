"""Lost acknowledgement after commit: retry must not duplicate a delivery.

The in-memory receiver models an atomic idempotency-key write. This fixture is
our example's own contract, not a claim about any vendor's API.
"""
import asyncio


def build(ctx):
    deliveries = []
    completed = set()
    attempts = []
    broken = ctx.inputs.get('broken', False)

    async def deliver():
        for attempt in range(2):
            attempts.append(attempt)
            if broken or 'invoice-42' not in completed:
                deliveries.append({'id': 'invoice-42', 'amount': 1200})
                completed.add('invoice-42')
                ctx.record('delivery_committed', id='invoice-42', amount=1200)
            if attempt == 0:
                # First delivery committed, but its response was lost.
                await asyncio.sleep(5)
                continue
            break

    ctx.at(0, 'deliver-with-retry', deliver)
    ctx.expect('one durable delivery', lambda: deliveries, [{'id': 'invoice-42', 'amount': 1200}])
    ctx.expect('retried after lost acknowledgement', lambda: attempts, [0, 1])
