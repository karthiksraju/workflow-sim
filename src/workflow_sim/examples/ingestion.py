"""Restart a paginated import after data writes but before its checkpoint.

The interruption is an injected exception at a persistence boundary, not a real
process kill. Separate worker-loss conformance checks exercise process redelivery.
"""
import asyncio


class InterruptedImport(Exception):
    pass


class ImportState:
    def __init__(self):
        self.rows = [{'id': 'old', 'value': 'keep'}, {'id': 'a', 'value': 'stale'}]
        self.checkpoint = 0
        self.completed = False
        self.interrupted = False
        self.attempts = 0

    def write(self, records, *, append=False):
        for record in records:
            if not append:
                self.rows[:] = [r for r in self.rows if r['id'] != record['id']]
            self.rows.append(dict(record))


async def import_pages(pages, state, *, broken=False):
    state.attempts += 1
    while state.checkpoint < len(pages):
        await asyncio.sleep(1)
        state.write(pages[state.checkpoint], append=broken)
        if not state.interrupted:
            state.interrupted = True
            raise InterruptedImport('rows committed; checkpoint not committed')
        state.checkpoint += 1
    state.completed = True


def build(ctx):
    state = ImportState()
    pages = [[{'id': 'a', 'value': 'new'}, {'id': 'b', 'value': 'two'}],
             [{'id': 'c', 'value': 'three'}]]

    async def attempt():
        try:
            await import_pages(pages, state, broken=ctx.inputs.get('broken', False))
        except InterruptedImport:
            ctx.record('import_interrupted', checkpoint=state.checkpoint, completed=state.completed)

    ctx.at(0, 'first-import', attempt)
    ctx.at(3, 'restart-import', attempt)
    ctx.expect('exact imported rows and unrelated existing row', lambda: sorted(state.rows, key=lambda r: r['id']),
               [{'id': 'a', 'value': 'new'}, {'id': 'b', 'value': 'two'},
                {'id': 'c', 'value': 'three'}, {'id': 'old', 'value': 'keep'}])
    ctx.expect('checkpoint after final page', lambda: state.checkpoint, 2)
    ctx.expect('completed after recovery', lambda: state.completed, True)
    ctx.expect('interruption actually exercised', lambda: [state.interrupted, state.attempts], [True, 2])
