"""A late recording result must not overwrite a rescheduled occurrence.

Synthetic reduction of the meeting integration's generation-fencing pattern.
No provider payload or production customer data is included.
"""
import asyncio


class Meeting:
    def __init__(self):
        self.generation = 1
        self.results = {'previous-meeting': 'preserved'}
        self.discarded = []

    def reschedule(self):
        self.generation += 1

    async def process(self, text, delay, *, broken=False):
        generation = self.generation
        await asyncio.sleep(delay)
        if generation != self.generation and not broken:
            self.discarded.append(generation)
            return
        self.results['current-meeting'] = text


def build(ctx):
    meeting = Meeting()
    ctx.at(0, 'old-recording', lambda: meeting.process('old occurrence', 6,
                                                     broken=ctx.inputs.get('broken', False)))
    ctx.at(1, 'reschedule', meeting.reschedule)
    ctx.at(2, 'new-recording', lambda: meeting.process('new occurrence', 1))
    ctx.expect('late result cannot replace current content', lambda: meeting.results,
               {'previous-meeting': 'preserved', 'current-meeting': 'new occurrence'})
    ctx.expect('stale generation explicitly discarded', lambda: meeting.discarded, [1])
