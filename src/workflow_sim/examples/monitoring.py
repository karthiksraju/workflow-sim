"""Recovery and a new outage must invalidate an old delayed alert."""
import asyncio


class Monitor:
    def __init__(self):
        self.generation = 0
        self.down = False
        self.alerts = []

    async def outage(self, *, broken=False):
        self.generation += 1
        incident = self.generation
        self.down = True
        await asyncio.sleep(5)
        if self.down and (broken or incident == self.generation):
            self.alerts.append({'service': 'api', 'incident': incident})

    def recover(self):
        self.generation += 1
        self.down = False


def build(ctx):
    monitor = Monitor()
    ctx.at(0, 'first-outage', lambda: monitor.outage(broken=ctx.inputs.get('broken', False)))
    ctx.at(2, 'recovered', monitor.recover)
    ctx.at(4, 'new-outage', lambda: monitor.outage(broken=ctx.inputs.get('broken', False)))
    ctx.expect('only current incident alerts after grace period', lambda: monitor.alerts,
               [{'service': 'api', 'incident': 3}])
    ctx.expect('current outage remains active', lambda: [monitor.down, monitor.generation], [True, 3])
