"""Bound retrieval latency while preserving independent successful routes."""

import asyncio

from astrbot.api import logger


async def search_route(name, awaitable, timeout=10.0):
    """Execute one route without allowing an ordinary failure to escape.

    Args:
        name: Route name used for diagnostics.
        awaitable: Retrieval coroutine.
        timeout: Maximum duration in seconds.

    Returns:
        A pair of results and an optional exception. Cancellation propagates.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout), None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Retrieval route %s failed: %s", name, exc)
        return [], exc
