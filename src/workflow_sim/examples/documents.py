"""AI/document fan-out: retry a transient failure and await every chunk.

Responses are fixed synthetic extracted text. This tests orchestration and content
assembly, not OCR/LLM accuracy, model quality or a vendor SDK.
"""
import asyncio


class Extractor:
    def __init__(self):
        self.attempts = {0: 0, 1: 0, 2: 0}
        self.ready = []
        self.documents = {'existing': 'untouched'}
        self.ready_at_commit = []

    async def extract(self, index, text):
        self.attempts[index] += 1
        await asyncio.sleep([1, 2, 6][index])
        if index == 1 and self.attempts[index] == 1:
            raise TimeoutError('transient extractor timeout')
        self.ready.append(index)
        return index, text.upper()


async def process_document(chunks, extractor, *, broken=False):
    async def retry(index, text):
        try:
            return await extractor.extract(index, text)
        except TimeoutError:
            await asyncio.sleep(1)
            return await extractor.extract(index, text)

    tasks = [asyncio.create_task(retry(i, text)) for i, text in enumerate(chunks)]
    if broken:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        pieces = [task.result() for task in done]
    else:
        pieces = await asyncio.gather(*tasks)
    extractor.ready_at_commit = sorted(extractor.ready)
    extractor.documents['doc-42'] = '\n'.join(text for _, text in sorted(pieces))
    if broken:
        await asyncio.gather(*pending)  # finishing later cannot repair the early commit


def build(ctx):
    extractor = Extractor()
    ctx.at(0, 'extract-document', lambda: process_document(['first', 'second', 'third'], extractor,
                                                         broken=ctx.inputs.get('broken', False)))
    ctx.expect('all content in original order', lambda: extractor.documents,
               {'existing': 'untouched', 'doc-42': 'FIRST\nSECOND\nTHIRD'})
    ctx.expect('complete only after every chunk', lambda: extractor.ready_at_commit, [0, 1, 2])
    ctx.expect('only failed chunk retried', lambda: list(extractor.attempts.values()), [1, 2, 1])
