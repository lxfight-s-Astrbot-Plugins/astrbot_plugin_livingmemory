"""Runtime adapters for AstrBot APIs that changed across supported versions."""

from __future__ import annotations

import inspect
from typing import Any


async def get_using_provider(context: Any, umo: str | None = None):
    """Resolve the active chat provider with the host's preferred API."""
    getter = getattr(context, "get_using_provider_async", None)
    if inspect.iscoroutinefunction(getter):
        return await getter(umo)
    return context.get_using_provider(umo)
