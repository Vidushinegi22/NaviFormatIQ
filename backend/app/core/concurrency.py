"""The single chokepoint for running the ported (sync) services off the event loop.

Every call into ``app.services.*`` from an ``async def`` MUST go through
``run_sync(...)`` so python-docx / PyMuPDF / LibreOffice never block the loop.
``office_semaphore()`` serialises LibreOffice (soffice is single-instance).
"""
from __future__ import annotations

import asyncio
import functools
from typing import Awaitable, Callable, TypeVar

import anyio

T = TypeVar("T")


async def run_sync(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a blocking function in a worker thread."""
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await anyio.to_thread.run_sync(func, *args)


_office_sem: asyncio.Semaphore | None = None


def office_semaphore() -> asyncio.Semaphore:
    """Lazily-created semaphore that serialises LibreOffice conversions."""
    global _office_sem
    if _office_sem is None:
        _office_sem = asyncio.Semaphore(1)
    return _office_sem


async def run_office(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a LibreOffice-backed sync function, serialised + off-loop."""
    async with office_semaphore():
        return await run_sync(func, *args, **kwargs)
