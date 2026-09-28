"""Inventory reservation compensated after a payment decline.

Synthetic order/inventory ports. Atomic reservation and payment-decline semantics
are model assumptions, not claims about a database or payment provider.
"""
import asyncio


class PaymentDeclined(Exception):
    pass


class Store:
    def __init__(self, decline=True):
        self.available = 3  # two of five books already belong to another order
        self.reservations = {'older-order': 2}
        self.shipments = []
        self.charges = []
        self.decline = decline
        self.status = 'new'

    async def charge(self, order):
        await asyncio.sleep(1)
        if self.decline:
            raise PaymentDeclined(order['id'])
        self.charges.append({'order': order['id'], 'amount': order['amount']})


async def fulfill(order, store, *, broken=False):
    if store.available < order['quantity']:
        store.status = 'out-of-stock'
        return
    store.available -= order['quantity']
    store.reservations[order['id']] = order['quantity']
    try:
        await store.charge(order)
    except PaymentDeclined:
        if not broken:
            store.available += store.reservations.pop(order['id'])
        store.status = 'declined'
        return
    store.shipments.append({'order': order['id'], 'quantity': store.reservations.pop(order['id'])})
    store.status = 'fulfilled'


def build(ctx):
    decline = ctx.inputs.get('decline', True)
    store = Store(decline)
    order = {'id': 'order-42', 'quantity': 2, 'amount': 2400}
    ctx.at(0, 'reserve-charge-fulfill', lambda: fulfill(order, store, broken=ctx.inputs.get('broken', False)))
    ctx.expect('available stock restored on decline', lambda: store.available, 3 if decline else 1)
    ctx.expect('other reservation preserved', lambda: store.reservations, {'older-order': 2})
    ctx.expect('ship only paid orders', lambda: store.shipments,
               [] if decline else [{'order': 'order-42', 'quantity': 2}])
    ctx.expect('exact charges', lambda: store.charges, [] if decline else [{'order': 'order-42', 'amount': 2400}])
    ctx.expect('terminal order status', lambda: store.status, 'declined' if decline else 'fulfilled')
