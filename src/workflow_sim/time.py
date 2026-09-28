"""Optional clock seam for applications using the simulator."""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timezone
from typing import Optional, Protocol


class ClockProvider(Protocol):
    def now(self) -> datetime: ...
    def time(self) -> float: ...
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


_provider: Optional[ClockProvider] = None


def install(provider: ClockProvider) -> None:
    """Route the seam through ``provider`` (the simulator's virtual clock)."""
    global _provider
    _provider = provider


def uninstall() -> None:
    global _provider
    _provider = None


def installed() -> Optional[ClockProvider]:
    return _provider


def now() -> datetime:
    """Aware UTC ``datetime`` for the current instant."""
    if _provider is not None:
        return _provider.now()
    return datetime.now(timezone.utc)


def time() -> float:
    """Seconds since the epoch, like ``time.time()``."""
    if _provider is not None:
        return _provider.time()
    return _time.time()


def monotonic() -> float:
    """Monotonic seconds, like ``time.monotonic()``."""
    if _provider is not None:
        return _provider.monotonic()
    return _time.monotonic()


async def sleep(seconds: float) -> None:
    """``asyncio.sleep`` routed through the seam so a virtual clock can jump."""
    if _provider is not None:
        await _provider.sleep(seconds)
        return
    await asyncio.sleep(seconds)
