"""Tests for runtime compatibility with different AstrBot API generations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_livingmemory.core.astrbot_compat import get_using_provider


@pytest.mark.asyncio
async def test_get_using_provider_prefers_async_api():
    provider = object()
    get_async = AsyncMock(return_value=provider)
    get_sync = Mock(side_effect=AssertionError("sync API must not be used"))
    context = SimpleNamespace(
        get_using_provider_async=get_async,
        get_using_provider=get_sync,
    )

    assert await get_using_provider(context, "platform:FriendMessage:1") is provider
    get_async.assert_awaited_once_with("platform:FriendMessage:1")
    get_sync.assert_not_called()


@pytest.mark.asyncio
async def test_get_using_provider_falls_back_for_older_astrbot():
    provider = object()
    get_sync = Mock(return_value=provider)
    context = SimpleNamespace(get_using_provider=get_sync)

    assert await get_using_provider(context) is provider
    get_sync.assert_called_once_with(None)
