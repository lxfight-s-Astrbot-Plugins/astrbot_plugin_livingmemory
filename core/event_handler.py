"""
事件处理器
负责处理AstrBot事件钩子
"""

import asyncio
import hashlib
import re
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from astrbot.api.provider import LLMResponse, ProviderRequest

from .base.config_manager import ConfigManager
from .base.constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)
from .event_handler_modules import (
    GroupCapture,
    MemoryRecall,
    MemoryReflection,
    MessageUtils,
)
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .processors.memory_processor import MemoryProcessor
from .utils import (
    OperationContext,
    format_memories_for_fake_tool_call,
    format_memories_for_injection,
    get_persona_id,
)
from .utils.injection_adapter import InjectionAdapter

# 预编译记忆注入清理正则（热路径优化：避免每次调用 re.compile）
_INJECTION_CLEANUP_PATTERN = re.compile(
    re.escape(MEMORY_INJECTION_HEADER) + r".*?" + re.escape(MEMORY_INJECTION_FOOTER),
    flags=re.DOTALL,
)


class EventHandler:
    """事件处理器"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        memory_processor: MemoryProcessor,
        conversation_manager: ConversationManager,
    ):
        """
        初始化事件处理器

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            memory_processor: 记忆处理器
            conversation_manager: 会话管理器
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.conversation_manager = conversation_manager

        # 初始化子模块
        self._message_utils = MessageUtils(config_manager, conversation_manager)
        self._group_capture = GroupCapture(
            config_manager, conversation_manager, self._message_utils
        )
        self._injection_adapter = InjectionAdapter()
        self._memory_recall = MemoryRecall(
            context,
            config_manager,
            memory_engine,
            conversation_manager,
            self._message_utils,
            self._injection_adapter,
        )

        # 后台存储任务跟踪
        self._storage_tasks: set[asyncio.Task] = set()
        self._storage_sessions_inflight: set[str] = set()
        self._storage_state_lock = asyncio.Lock()
        self._shutting_down = False

        self._memory_reflection = MemoryReflection(
            context,
            config_manager,
            memory_engine,
            memory_processor,
            conversation_manager,
            self._message_utils,
            self._storage_tasks,
            self._storage_sessions_inflight,
            self._storage_state_lock,
        )

    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """Capture all group messages for memory storage"""
        await self._group_capture.handle_all_group_messages(event)

    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """Query and inject long-term memory before LLM request"""
        await self._memory_recall.handle_memory_recall(event, req)

    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """Check if reflection and memory storage is needed after LLM response"""
        await self._memory_reflection.handle_memory_reflection(event, resp)

    async def handle_session_reset(self, event: AstrMessageEvent) -> None:
        """处理 /reset 或 /new 触发的会话清空，同步清除插件侧的消息历史和总结计数器"""
        session_id = event.unified_msg_origin
        if not session_id:
            return
        try:
            await self.conversation_manager.clear_session(session_id)
            logger.info(f"[{session_id}] 已同步清空插件会话上下文（/reset 或 /new）")
        except Exception as e:
            logger.error(f"[{session_id}] 清空插件会话上下文失败: {e}", exc_info=True)

    async def shutdown(self):
        """关闭事件处理器，等待所有存储任务完成"""
        self._shutting_down = True
        self._memory_reflection.set_shutting_down(True)
        if self._storage_tasks:
            logger.info(f"等待 {len(self._storage_tasks)} 个存储任务完成...")
            await asyncio.gather(*self._storage_tasks, return_exceptions=True)
            self._storage_tasks.clear()
        self._storage_sessions_inflight.clear()
        logger.info("EventHandler 已关闭")
