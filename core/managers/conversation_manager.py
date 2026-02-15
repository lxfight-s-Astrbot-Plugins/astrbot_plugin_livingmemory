"""
会话管理器 - ConversationManager
提供高级的会话和消息管理功能

功能:
- 会话生命周期管理
- 群聊场景支持
- AstrBot事件集成
"""

import json
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ...storage.conversation_store import ConversationStore
from ..models.conversation_models import Message, Session


class ConversationManager:
    """
    会话管理器 - 提供高级的会话和消息管理功能

    功能:
    - 会话生命周期管理
    - 群聊场景支持
    - AstrBot事件集成
    """

    def __init__(
        self,
        store: ConversationStore,
    ):
        """
        初始化会话管理器

        Args:
            store: ConversationStore实例
        """
        self.store = store
        logger.info("[ConversationManager] 初始化完成")

    async def add_message_from_event(
        self,
        event: Any,  # AstrBot MessageEvent
        role: str,
        content: str,
        session_id: str | None = None,
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
        # 默认使用 unified_msg_origin 作为会话ID，允许调用方传入更细粒度的会话键
        effective_session_id = session_id or event.unified_msg_origin

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
            sender_id = effective_session_id

        # 尝试获取发送者昵称
        if hasattr(event, "get_sender_name"):
            sender_name = event.get_sender_name()
        elif hasattr(event, "sender_name"):
            sender_name = event.sender_name

        # Debug: 记录原始 message_obj.sender 信息
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "sender"):
            raw_sender = event.message_obj.sender
            logger.debug(
                f"[add_message_from_event] [{effective_session_id}] 原始sender对象: "
                f"user_id={getattr(raw_sender, 'user_id', 'N/A')}, "
                f"nickname={getattr(raw_sender, 'nickname', 'N/A')}"
            )

        # 判断是否群聊（使用 get_message_type 而非 is_group，更可靠）
        is_group = False
        if hasattr(event, "get_message_type"):
            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if is_group:
                # 群组ID保持为消息来源标识，而不是插件内部会话键
                group_id = event.unified_msg_origin

        # 调试日志：记录最终获取到的发送者信息
        logger.debug(
            f"[add_message_from_event] [{effective_session_id}] 最终发送者信息: "
            f"sender_id={sender_id}, sender_name='{sender_name}', "
            f"role={role}, is_group={is_group}, group_id={group_id}"
        )

        # 获取平台名称（字符串）
        platform = (
            event.get_platform_name()
            if hasattr(event, "get_platform_name")
            else "unknown"
        )

        return await self.add_message(
            session_id=effective_session_id,
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
        sender_id: str | None = None,
        sender_name: str | None = None,
        group_id: str | None = None,
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

    async def get_context(
        self,
        session_id: str,
        max_messages: int | None = None,
        sender_id: str | None = None,
        format_for_llm: bool = True,
    ) -> list[dict[str, str]]:
        """
        获取会话上下文(用于LLM)

        Args:
            session_id: 会话ID
            max_messages: 最大消息数（None 时使用默认 50）
            sender_id: 过滤特定发送者(群聊场景)
            format_for_llm: 是否格式化为LLM格式

        Returns:
            消息列表,格式: [{"role": "user", "content": "..."}, ...]
        """
        limit = max_messages if max_messages is not None else 50
        if limit <= 0:
            return []

        messages = await self.get_messages(
            session_id=session_id,
            limit=limit,
            sender_id=sender_id,
        )

        if format_for_llm:
            # 只在群聊场景(有group_id)时添加发送者名称前缀
            return [
                msg.format_for_llm(include_sender_name=bool(msg.group_id))
                for msg in messages
            ]

        return [msg.to_dict() for msg in messages]

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        sender_id: str | None = None,
    ) -> list[Message]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制数量
            sender_id: 过滤发送者

        Returns:
            Message对象列表（按时间升序）
        """
        if limit <= 0:
            return []
        return await self.store.get_messages(
            session_id=session_id, limit=limit, sender_id=sender_id
        )

    async def get_session_info(self, session_id: str) -> Session | None:
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

    async def get_recent_sessions(self, limit: int | None = 10) -> list[Session]:
        """
        获取最近活跃的会话

        Args:
            limit: 返回数量限制，None 表示不限制

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

        # 同步重置会话元数据，特别是记忆总结的计数器
        await self.reset_session_metadata(session_id)

        logger.info(f"[ConversationManager] 已清空会话并重置记忆上下文: {session_id}")

    async def get_messages_range(
        self, session_id: str, start_index: int = 0, end_index: int | None = None
    ) -> list[Message]:
        """
        获取指定范围的消息（用于滑动窗口总结）

        Args:
            session_id: 会话ID
            start_index: 起始消息索引（从0开始，包含）
            end_index: 结束消息索引（不包含），None表示到最后

        Returns:
            Message对象列表
        """
        # 先获取会话信息以确定消息总数
        session_info = await self.get_session_info(session_id)
        if not session_info:
            logger.warning(f"[get_messages_range] 会话 {session_id} 不存在")
            return []

        recorded_count = session_info.message_count

        # 获取实际消息数量（用于一致性检查）
        actual_count = await self.store.get_message_count(session_id)

        # 数据一致性检查：如果 sessions 表记录的 message_count 与实际不符
        if recorded_count != actual_count:
            logger.warning(
                f"[get_messages_range] [{session_id}] 数据不一致! "
                f"sessions表记录={recorded_count}, 实际消息数={actual_count}，正在同步..."
            )
            # 使用实际消息数量，并触发同步修复
            await self.store.sync_message_counts()

        total_messages = actual_count  # 使用实际消息数量

        # 确定实际需要获取的范围
        actual_end = end_index if end_index is not None else total_messages

        # 验证索引范围
        if start_index < 0:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} < 0，调整为 0"
            )
            start_index = 0

        if start_index >= total_messages:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} >= 实际消息总数 {total_messages}，返回空列表"
            )
            return []

        if actual_end > total_messages:
            logger.warning(
                f"[get_messages_range] [{session_id}] 结束索引 {actual_end} 超出范围，调整为 {total_messages}"
            )
            actual_end = total_messages

        if start_index >= actual_end:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} >= 结束索引 {actual_end}，返回空列表"
            )
            return []

        # 计算需要获取的消息数量
        needed_count = actual_end - start_index

        logger.debug(
            f"[get_messages_range] [{session_id}] 准备获取消息: "
            f"实际总数={total_messages}, 范围=[{start_index}:{actual_end}], "
            f"需要={needed_count}条"
        )

        # 使用 store 的 get_messages_range 方法（基于 OFFSET/LIMIT）
        result = await self.store.get_messages_range(
            session_id=session_id,
            offset=start_index,
            limit=needed_count,
        )

        logger.info(
            f"[get_messages_range] [{session_id}] 返回 {len(result)} 条消息 (索引 {start_index} 到 {actual_end})"
        )

        return result

    async def update_session_metadata(
        self, session_id: str, key: str, value: Any
    ) -> None:
        """
        更新会话元数据

        Args:
            session_id: 会话ID
            key: 元数据键
            value: 元数据值
        """
        session = await self.store.get_session(session_id)
        if not session:
            logger.warning(
                f"[ConversationManager] 会话 {session_id} 不存在，无法更新元数据"
            )
            return

        # 更新元数据
        session.metadata[key] = value

        # 保存到数据库
        if self.store.connection is not None:
            try:
                await self.store.connection.execute(
                    """
                    UPDATE sessions
                    SET metadata = ?
                    WHERE session_id = ?
                """,
                    (json.dumps(session.metadata, ensure_ascii=False), session_id),
                )
                await self.store.connection.commit()
            except Exception as e:
                logger.error(f"更新会话元数据失败: {e}", exc_info=True)

        logger.debug(
            f"[ConversationManager] 更新会话元数据: {session_id}, {key}={value}"
        )

    async def get_session_metadata(
        self, session_id: str, key: str, default: Any = None
    ) -> Any:
        """
        获取会话元数据

        Args:
            session_id: 会话ID
            key: 元数据键
            default: 默认值

        Returns:
            元数据值，不存在则返回default
        """
        session = await self.store.get_session(session_id)
        if not session:
            return default

        return session.metadata.get(key, default)

    async def reset_session_metadata(self, session_id: str) -> None:
        """
        重置指定会话的所有元数据，特别是 'last_summarized_index'。
        这会使下一次记忆总结从头开始，不会包含旧的上下文。
        """
        session = await self.store.get_session(session_id)
        if not session:
            logger.warning(
                f"[ConversationManager] 尝试重置元数据失败，会话 {session_id} 不存在"
            )
            return
        # 将元数据重置为空字典
        session.metadata = {}
        # 保存回数据库
        if self.store.connection is not None:
            try:
                await self.store.connection.execute(
                    """
                    UPDATE sessions
                    SET metadata = ?
                    WHERE session_id = ?
                """,
                    ("{}", session_id),
                )
                await self.store.connection.commit()
            except Exception as e:
                logger.error(f"重置会话元数据失败: {e}", exc_info=True)
        logger.info(
            f"[ConversationManager] 已重置会话 {session_id} 的元数据 (记忆总结计数器已清零)"
        )


def create_conversation_manager(
    db_path: str, config: dict[str, Any] | None = None
) -> ConversationManager:
    """
    便捷创建函数

    Args:
        db_path: 数据库路径
        config: 预留参数（当前未使用）

    Returns:
        ConversationManager实例
    """
    _ = config
    store = ConversationStore(db_path)

    return ConversationManager(store=store)
