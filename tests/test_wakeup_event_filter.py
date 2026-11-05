"""Issue #389 回归：框架合成的唤醒轮次不写入会话历史。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore

from astrbot.api.platform import MessageType


class _Event:
    """最小化的 AstrBot 事件替身，覆盖消息写入所需字段。"""

    def __init__(
        self,
        umo: str = "aiocqhttp:private:u1",
        platform: str = "aiocqhttp",
        event_kind: str = "message",
    ):
        self.unified_msg_origin = umo
        self._platform = platform
        self.event_kind = event_kind
        self.sender_id = "1001234"
        self.sender_name = "小明"
        self.message_obj = SimpleNamespace(timestamp=None)

    def get_platform_name(self) -> str:
        return self._platform

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_sender_name(self) -> str:
        return self.sender_name

    def get_message_type(self):
        return MessageType.FRIEND_MESSAGE


@pytest.mark.asyncio
async def test_agent_wakeup_messages_are_not_stored(tmp_path: Path) -> None:
    """新版 AstrBot：定时任务/后台任务复用真实会话，靠事件类型识别。"""
    store = ConversationStore(str(tmp_path / "wakeup.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    umo = "aiocqhttp:private:u1"

    user_written = await manager.add_message_from_event(
        _Event(umo, event_kind="agent_wakeup"),
        role="user",
        content="[定时任务] 提醒用户喝水",
    )
    assistant_written = await manager.add_message_from_event(
        _Event(umo, event_kind="agent_wakeup"),
        role="assistant",
        content="已提醒",
    )

    assert user_written is None
    assert assistant_written is None
    assert await store.get_message_count(umo) == 0
    await store.close()


@pytest.mark.asyncio
async def test_legacy_cron_platform_messages_are_not_stored(tmp_path: Path) -> None:
    """4.29.4 及更早版本：定时任务事件平台为 cron。"""
    store = ConversationStore(str(tmp_path / "cron.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    umo = "aiocqhttp:private:u1"

    written = await manager.add_message_from_event(
        _Event(umo, platform="cron"),
        role="assistant",
        content="定时任务结果",
    )

    assert written is None
    assert await store.get_message_count(umo) == 0
    await store.close()


@pytest.mark.asyncio
async def test_real_user_messages_are_still_stored(tmp_path: Path) -> None:
    store = ConversationStore(str(tmp_path / "real.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    umo = "aiocqhttp:private:u1"

    message = await manager.add_message_from_event(
        _Event(umo), role="user", content="你好"
    )

    assert message is not None
    assert await store.get_message_count(umo) == 1
    await store.close()
