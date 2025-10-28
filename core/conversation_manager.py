# -*- coding: utf-8 -*-
"""
会话管理器 - ConversationManager
提供高级的会话和消息管理功能

功能:
- 会话生命周期管理
- LRU缓存热点会话
- 上下文窗口管理
- 群聊场景支持
- AstrBot事件集成
"""

from typing import List, Optional, Dict, Any
from collections import OrderedDict
import time

from ..storage.conversation_store import ConversationStore
from ..core.conversation_models import Message, Session
from astrbot.api import logger


class ConversationManager:
    """
    会话管理器 - 提供高级的会话和消息管理功能

    功能:
    - 会话生命周期管理
    - LRU缓存热点会话
    - 上下文窗口管理
    - 群聊场景支持
    - AstrBot事件集成
    """

    def __init__(
        self,
        store: ConversationStore,
        max_cache_size: int = 100,
        context_window_size: int = 50,
        session_ttl: int = 3600,
    ):
        """
        初始化会话管理器

        Args:
            store: ConversationStore实例
            max_cache_size: LRU缓存大小
            context_window_size: 上下文窗口大小(保留最近N条消息)
            session_ttl: 会话过期时间(秒)
        """
        self.store = store
        self.max_cache_size = max_cache_size
        self.context_window_size = context_window_size
        self.session_ttl = session_ttl

        # LRU缓存: {session_id: (messages, last_access_time)}
        self._cache: OrderedDict = OrderedDict()

        logger.info(
            f"[ConversationManager] 初始化完成: "
            f"缓存大小={max_cache_size}, 上下文窗口={context_window_size}"
        )

    async def add_message_from_event(
        self,
        event: Any,  # AstrBot MessageEvent
        role: str,
        content: str,
    ) -> Message:
        """
        从AstrBot事件添加消息(自动提取发送者信息)

        Args:
            event: AstrBot的MessageEvent对象
            role: 消息角色 ("user" 或 "assistant")
            content: 消息内容

        Returns:
            创建的Message对象
        """
        # 提取会话ID
        session_id = event.session_id

        # 提取发送者信息
        sender_id = None
        sender_name = None
        group_id = None

        # 尝试获取发送者ID
        if hasattr(event, "get_sender_id"):
            sender_id = event.get_sender_id()
        elif hasattr(event, "sender_id"):
            sender_id = event.sender_id

        # 如果还是没有sender_id,使用session_id作为后备
        if not sender_id:
            sender_id = session_id

        # 尝试获取发送者昵称
        if hasattr(event, "get_sender_name"):
            sender_name = event.get_sender_name()
        elif hasattr(event, "sender_name"):
            sender_name = event.sender_name

        # 判断是否群聊
        if hasattr(event, "is_group"):
            is_group = event.is_group()
            if is_group:
                group_id = session_id  # 群聊时session_id即为group_id

        # 获取平台名称（字符串）
        platform = event.get_platform_name() if hasattr(event, "get_platform_name") else "unknown"

        return await self.add_message(
            session_id=session_id,
            role=role,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            platform=platform,
        )

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sender_id: Optional[str] = None,
        sender_name: Optional[str] = None,
        group_id: Optional[str] = None,
        platform: str = "unknown",
    ) -> Message:
        """
        添加消息到会话

        Args:
            session_id: 会话ID
            role: 角色 ("user" 或 "assistant")
            content: 消息内容
            sender_id: 发送者ID
            sender_name: 发送者昵称
            group_id: 群组ID(群聊场景)
            platform: 平台标识

        Returns:
            创建的Message对象
        """
        # 如果没有sender_id,使用session_id
        if not sender_id:
            sender_id = session_id

        # 创建消息对象
        message = Message(
            id=0,  # 将由数据库分配
            session_id=session_id,
            role=role,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            platform=platform,
            timestamp=time.time(),
            metadata={},
        )

        # 存储到数据库
        message_id = await self.store.add_message(message)
        message.id = message_id

        # 使缓存失效(下次获取时重新加载)
        if session_id in self._cache:
            del self._cache[session_id]

        logger.debug(
            f"[ConversationManager] 添加消息: session={session_id}, "
            f"role={role}, sender={sender_id}"
        )
        
        # 添加后获取最新的消息统计
        session_info = await self.store.get_session(session_id)
        if session_info:
            logger.debug(
                f"[DEBUG-AddMessage] [{session_id}] 添加消息后，当前总消息数: {session_info.message_count}"
            )

        return message

    async def get_context(
        self,
        session_id: str,
        max_messages: Optional[int] = None,
        sender_id: Optional[str] = None,
        format_for_llm: bool = True,
    ) -> List[Dict[str, str]]:
        """
        获取会话上下文(用于LLM)

        Args:
            session_id: 会话ID
            max_messages: 最大消息数(None则使用context_window_size)
            sender_id: 过滤特定发送者(群聊场景)
            format_for_llm: 是否格式化为LLM格式

        Returns:
            消息列表,格式: [{"role": "user", "content": "..."}, ...]
        """
        limit = max_messages or self.context_window_size

        # 获取消息
        messages = await self.get_messages(
            session_id=session_id, limit=limit, sender_id=sender_id, use_cache=True
        )

        if format_for_llm:
            # 格式化为LLM格式
            # 只在群聊场景(有group_id)时添加发送者名称前缀
            return [
                msg.format_for_llm(include_sender_name=bool(msg.group_id))
                for msg in messages
            ]
        else:
            # 返回原始格式
            return [msg.to_dict() for msg in messages]

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        sender_id: Optional[str] = None,
        use_cache: bool = True,
    ) -> List[Message]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制数量
            sender_id: 过滤发送者
            use_cache: 是否使用缓存

        Returns:
            Message对象列表
        """
        # 如果指定了sender_id,不使用缓存(需要过滤)
        if sender_id:
            use_cache = False

        # 尝试从缓存获取
        if use_cache:
            cached_messages = self._get_from_cache(session_id)
            if cached_messages is not None:
                # 从缓存中截取需要的数量
                return cached_messages[-limit:] if limit else cached_messages

        # 从数据库获取
        messages = await self.store.get_messages(
            session_id=session_id, limit=limit, sender_id=sender_id
        )

        # 更新缓存(仅当不是过滤查询时)
        if not sender_id and use_cache:
            self._update_cache(session_id, messages)

        return messages

    async def create_or_get_session(
        self, session_id: str, platform: str = "unknown"
    ) -> Session:
        """
        创建或获取会话

        Args:
            session_id: 会话ID
            platform: 平台标识

        Returns:
            Session对象
        """
        # 尝试获取现有会话
        session = await self.store.get_session(session_id)

        if session:
            # 更新活跃时间
            await self.store.update_session_activity(session_id)
            return session

        # 创建新会话
        session = await self.store.create_session(session_id, platform)
        logger.info(f"[ConversationManager] 创建新会话: {session_id}")

        return session

    async def get_session_info(self, session_id: str) -> Optional[Session]:
        """
        获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            Session对象,不存在则返回None
        """
        session = await self.store.get_session(session_id)
        if session:
            logger.debug(
                f"[DEBUG-SessionInfo] [{session_id}] 会话信息: "
                f"message_count={session.message_count}, "
                f"created_at={session.created_at}, "
                f"last_active_at={session.last_active_at}"
            )
        else:
            logger.warning(f"[DEBUG-SessionInfo] [{session_id}] 会话不存在")
        return session

    async def get_recent_sessions(self, limit: int = 10) -> List[Session]:
        """
        获取最近活跃的会话

        Args:
            limit: 返回数量限制

        Returns:
            Session对象列表
        """
        return await self.store.get_recent_sessions(limit)

    async def clear_session(self, session_id: str):
        """
        清空会话历史

        Args:
            session_id: 会话ID
        """
        # 删除数据库中的消息
        await self.store.delete_session_messages(session_id)

        # 清除缓存
        if session_id in self._cache:
            del self._cache[session_id]

        logger.info(f"[ConversationManager] 清空会话: {session_id}")

    async def cleanup_expired_sessions(self) -> int:
        """
        清理过期会话

        Returns:
            清理的会话数量
        """
        # 计算过期时间(以天为单位)
        days = self.session_ttl // (24 * 3600)
        if days < 1:
            days = 30  # 默认30天

        deleted_count = await self.store.delete_old_sessions(days)

        # 清空缓存(可能包含已删除的会话)
        self._cache.clear()

        if deleted_count > 0:
            logger.info(f"[ConversationManager] 清理过期会话: {deleted_count}个")

        return deleted_count

    def _update_cache(self, session_id: str, messages: List[Message]):
        """
        更新LRU缓存

        Args:
            session_id: 会话ID
            messages: 消息列表
        """
        # 如果已存在,先删除(会被添加到末尾)
        if session_id in self._cache:
            del self._cache[session_id]

        # 添加到末尾(最新)
        self._cache[session_id] = (messages, time.time())

        # 如果超过容量,删除最旧的
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)  # 删除最前面的(最旧)

    def _get_from_cache(self, session_id: str) -> Optional[List[Message]]:
        """
        从缓存获取消息

        Args:
            session_id: 会话ID

        Returns:
            消息列表,不存在则返回None
        """
        if session_id in self._cache:
            messages, _ = self._cache[session_id]
            # 移到末尾(标记为最新访问)
            self._cache.move_to_end(session_id)
            # 更新访问时间
            self._cache[session_id] = (messages, time.time())
            return messages
        return None

    def _evict_cache(self):
        """
        LRU缓存驱逐(超过max_cache_size时)

        这个方法在_update_cache中已经处理,这里保留作为显式接口
        """
        while len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)


def create_conversation_manager(
    db_path: str, config: Optional[Dict[str, Any]] = None
) -> ConversationManager:
    """
    便捷创建函数

    Args:
        db_path: 数据库路径
        config: 配置字典,可包含:
            - max_cache_size: LRU缓存大小
            - context_window_size: 上下文窗口大小
            - session_ttl: 会话过期时间

    Returns:
        ConversationManager实例
    """
    config = config or {}
    store = ConversationStore(db_path)

    return ConversationManager(
        store=store,
        max_cache_size=config.get("max_cache_size", 100),
        context_window_size=config.get("context_window_size", 50),
        session_ttl=config.get("session_ttl", 3600),
    )
