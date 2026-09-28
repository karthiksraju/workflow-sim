from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_READY = threading.Event()
_LOOP_LOCK = threading.Lock()
_CANCELLATION_WAIT_SECONDS = 5.0

logger = logging.getLogger(__name__)


def _loop_thread_main() -> None:
    """Run a dedicated background event loop for sync->async bridging."""
    global _LOOP
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _LOOP = loop
    _LOOP_READY.set()
    loop.run_forever()


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD

    if (
        _LOOP is not None
        and not _LOOP.is_closed()
        and _LOOP_THREAD
        and _LOOP_THREAD.is_alive()
    ):
        return _LOOP

    with _LOOP_LOCK:
        if (
            _LOOP is not None
            and not _LOOP.is_closed()
            and _LOOP_THREAD
            and _LOOP_THREAD.is_alive()
        ):
            return _LOOP

        _LOOP = None
        _LOOP_READY.clear()
        _LOOP_THREAD = threading.Thread(
            target=_loop_thread_main,
            name="workflow-sim-loop",
            daemon=True,
        )
        _LOOP_THREAD.start()

    if not _LOOP_READY.wait(timeout=5):
        raise RuntimeError("Background asyncio loop failed to start")
    if _LOOP is None:
        raise RuntimeError("Background asyncio loop unavailable")
    return _LOOP


async def _run_with_completion_signal(
    coro: Coroutine[Any, Any, T], completion: threading.Event
) -> T:
    try:
        return await coro
    finally:
        completion.set()


def _wait_for_result(future):
    return future.result()


def run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from sync code using a persistent background loop.

    This avoids creating/closing a fresh event loop per call, which can break
    long-lived async clients (e.g. httpx.AsyncClient) under Celery workers.
    """
    loop = _ensure_background_loop()
    completion = threading.Event()
    future = asyncio.run_coroutine_threadsafe(
        _run_with_completion_signal(coro, completion), loop
    )
    try:
        return _wait_for_result(future)
    except BaseException:
        # Celery soft-time-limit exceptions interrupt the synchronous wait but
        # do not automatically cancel work submitted to this background loop.
        # Cancel and let coroutine cleanup settle before the caller releases
        # any execution lock or records a terminal failure.
        future.cancel()
        if not completion.wait(timeout=_CANCELLATION_WAIT_SECONDS):
            logger.error(
                "Async coroutine did not settle within %.1fs after cancellation; "
                "waiting for cleanup before releasing caller ownership",
                _CANCELLATION_WAIT_SECONDS,
            )
            completion.wait()
        raise


def submit_coro_background(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule a coroutine on the persistent background loop WITHOUT waiting.

    Fire-and-forget counterpart of ``run_coro_sync`` — the caller returns
    immediately; the coroutine is responsible for its own error handling
    (unhandled exceptions are swallowed by the future).
    """
    loop = _ensure_background_loop()
    asyncio.run_coroutine_threadsafe(coro, loop)
