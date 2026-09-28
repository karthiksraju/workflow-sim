"""Shared ordinary-asyncio workload; only documented asyncio APIs are used."""
import asyncio


async def execute(specs):
    attempts = [0] * len(specs)

    async def worker(index, spec):
        for attempt in range(spec['failures'] + 1):
            attempts[index] += 1
            await asyncio.sleep(spec['delay_ms'] / 1000)
            try:
                if attempt < spec['failures']:
                    raise TimeoutError('transient boundary response')
                if spec['pool']:
                    value = await asyncio.to_thread(lambda: spec['value'] * 2)
                else:
                    value = spec['value'] * 2
                future = asyncio.get_running_loop().create_future()
                future.set_result(value)
                return index, await future
            except TimeoutError:
                await asyncio.sleep(0)

    # An explicitly cancelled child cannot perform its forbidden side effect.
    parked = asyncio.Event()
    effects = []

    async def cancelled_child():
        await parked.wait()
        effects.append('forbidden')

    child = asyncio.create_task(cancelled_child())
    await asyncio.sleep(0)
    child.cancel()
    try:
        await child
    except asyncio.CancelledError:
        pass
    values = await asyncio.gather(*(worker(i, spec) for i, spec in enumerate(specs)))
    return {'values': [value for _, value in sorted(values)], 'attempts': attempts, 'effects': effects}


def build(ctx):
    actual = {}

    async def work():
        actual.update(await execute(ctx.inputs['specs']))

    ctx.at(0, 'generated-program', work)
    ctx.expect('complete program state', lambda: actual, ctx.inputs['expected'])


def lifecycle(ctx):
    completed = []
    retained = []

    async def child(index, spec):
        await asyncio.sleep(spec['delay'])
        completed.append(index)
        if spec['error']:
            raise ValueError(f'child-error-{index}')

    async def parent():
        for index, spec in enumerate(ctx.inputs['children']):
            task = asyncio.create_task(child(index, spec))
            retained.append(task)  # GC must not decide whether an error is visible
            if spec['cancel']:
                task.cancel()

    ctx.at(0, 'return-with-children', parent)
    ctx.expect('child effects through horizon', lambda: sorted(completed), ctx.inputs['expected'])
