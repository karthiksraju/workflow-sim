"""Payment webhook provisioning with a committed receipt whose response is lost.

Event fields follow Stripe's documented invoice.paid envelope; values are synthetic.
Account storage and atomic receipt idempotency are explicit example contracts.
"""
import asyncio


class Account:
    def __init__(self):
        self.plan = 'trial'
        self.processed = set()
        self.receipts = {}
        self.attempts = 0
        self.lost_response = False

    async def send_receipt(self, key, payload):
        self.attempts += 1
        self.receipts.setdefault(key, dict(payload))  # modeled atomic receiver write
        if not self.lost_response:
            self.lost_response = True
            raise TimeoutError('receipt committed; acknowledgement lost')


async def provision(event, account, *, broken=False):
    if event['id'] in account.processed:
        return
    invoice = event['data']['object']
    account.plan = 'paid'
    for attempt in range(3):
        key = f"{invoice['id']}:{attempt}" if broken else invoice['id']
        try:
            await account.send_receipt(key, {'invoice': invoice['id'],
                                            'customer': invoice['customer'],
                                            'amount': invoice['amount_paid']})
            account.processed.add(event['id'])
            return
        except TimeoutError:
            if attempt == 2:
                raise
            await asyncio.sleep(2)


def build(ctx):
    account = Account()
    event = {'id': 'evt-paid-42', 'type': 'invoice.paid', 'data': {'object': {
        'id': 'in-42', 'customer': 'cus-7', 'amount_paid': 1200}}}
    ctx.at(0, 'payment', lambda: provision(event, account, broken=ctx.inputs.get('broken', False)))
    ctx.at(5, 'duplicate-webhook', lambda: provision(event, account))
    ctx.expect('paid account', lambda: account.plan, 'paid')
    ctx.expect('one exact receipt', lambda: list(account.receipts.values()),
               [{'invoice': 'in-42', 'customer': 'cus-7', 'amount': 1200}])
    ctx.expect('acknowledgement retried', lambda: account.attempts, 2)
    ctx.expect('completion recorded after delivery', lambda: sorted(account.processed), ['evt-paid-42'])
