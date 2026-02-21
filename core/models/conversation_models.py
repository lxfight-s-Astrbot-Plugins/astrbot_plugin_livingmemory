"""
数据模型定义 - LivingMemory 插件重构
包含 Message、Session、MemoryEvent 三个核心数据模型
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from astrbot.api import logger


@dataclass
class Message:
    """
    单条消息记录 - 支持群聊场景

    用于表示对话中的单条消息,包含发送者信息、内容、时间戳等
    """

    # 基础字段
    id: int  # 消息ID (数据库自增主键)
    session_id: str  # 会话ID (外键)
    role: str  # 角色: "user" | "assistant" | "system"
    content: str  # 消息文本内容

    # 发送者信息 (群聊关键字段)
    sender_id: str  # 发送者唯一ID (用户ID/群组成员ID)
    sender_name: str | None = None  # 发送者昵称 (用于显示)

    # 上下文信息
    group_id: str | None = None  # 群组ID (私聊时为 None)
    platform: str | None = None  # 平台标识 (如 "qq", "discord")

    # 时间戳
    timestamp: float = field(default_factory=time.time)  # 消息创建时间

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外的元数据 (JSON)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "group_id": self.group_id,
            "platform": self.platform,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """从字典创建 Message 对象"""
        # 处理 metadata 可能是 JSON 字符串的情况
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}

        return cls(
            id=data.get("id", 0),
            session_id=data["session_id"],
            role=data["role"],
            content=data["content"],
            sender_id=data["sender_id"],
            sender_name=data.get("sender_name"),
            group_id=data.get("group_id"),
            platform=data.get("platform"),
            timestamp=data.get("timestamp", time.time()),
            metadata=metadata,
        )

    def format_for_llm(self, include_sender_name: bool = True) -> dict[str, str]:
        """
        格式化为 LLM 所需格式

        Args:
            include_sender_name: 是否在消息前添加发送者详细信息 (群聊场景)

        Returns:
            Dict: {"role": "user/assistant", "content": "..."}
        """

        content = self.content

        # 群聊场景: 在消息前加上发送者详细信息
        if include_sender_name and self.group_id:
            # 判断是否为Bot消息：优先检查 metadata 标记，其次 role
            is_bot = self.metadata.get("is_bot_message", False) or self.role == "assistant"

            # 格式化时间
            time_str = datetime.fromtimestamp(self.timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            # 确定显示的发送者名称：优先使用昵称，否则使用ID
            display_name = (
                self.sender_name if self.sender_name else self.sender_id or "未知"
            )

            # Debug: 记录发送者信息获取情况
            logger.debug(
                f"[format_for_llm] 消息格式化: "
                f"sender_id={self.sender_id}, sender_name={self.sender_name}, "
                f"display_name={display_name}, is_bot={is_bot}, role={self.role}"
            )

            # 构建发送者信息前缀
            # Bot消息使用 [Bot: 昵称] 格式
            # 其他群成员直接使用 [昵称] 格式，让LLM能清晰识别每个发送者
            if is_bot:
                sender_info = (
                    f"[Bot: {display_name} | ID: {self.sender_id} | {time_str}]"
                )
            else:
                sender_info = f"[{display_name} | ID: {self.sender_id} | {time_str}]"
            content = f"{sender_info} {content}"

        return {"role": self.role, "content": content}


@dataclass
class Session:
    """
    会话对象 - 表示一段对话

    支持私聊和群聊场景,记录会话元信息和统计数据
    """

    # 基础字段
    id: int  # 数据库主键 (自增)
    session_id: str  # 会话唯一标识
    platform: str  # 平台类型

    # 时间信息
    created_at: float  # 会话创建时间
    last_active_at: float  # 最后活跃时间

    # 统计信息
    message_count: int = 0  # 消息总数

    # 群聊相关
    participants: list[str] = field(default_factory=list)  # 参与者ID列表 (JSON存储)

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外的元数据 (JSON)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "platform": self.platform,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "message_count": self.message_count,
            "participants": self.participants,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """从字典创建 Session 对象"""
        # 处理 participants 可能是 JSON 字符串的情况
        participants = data.get("participants", [])
        if isinstance(participants, str):
            try:
                participants = json.loads(participants)
            except json.JSONDecodeError:
                participants = []

        # 处理 metadata 可能是 JSON 字符串的情况
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}

        return cls(
            id=data.get("id", 0),
            session_id=data["session_id"],
            platform=data["platform"],
            created_at=data["created_at"],
            last_active_at=data["last_active_at"],
            message_count=data.get("message_count", 0),
            participants=participants,
            metadata=metadata,
        )

    def add_participant(self, sender_id: str) -> None:
        """添加参与者 (避免重复)"""
        if sender_id not in self.participants:
            self.participants.append(sender_id)

    def update_activity(self) -> None:
        """更新最后活跃时间"""
        self.last_active_at = time.time()

    def increment_message_count(self) -> None:
        """增加消息计数"""
        self.message_count += 1


@dataclass
class MemoryEvent:
    """
    记忆事件 - 由反思引擎提取的结构化记忆

    用于存储从对话中提取的重要信息和事件
    """

    # 基础字段
    memory_content: str  # 记忆内容 (文本描述)
    importance_score: float  # 重要性分数 (0.0 - 1.0)

    # 关联信息
    session_id: str  # 关联的会话ID

    # 时间信息
    timestamp: float = field(default_factory=time.time)  # 记忆创建时间

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外的元数据

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "memory_content": self.memory_content,
            "importance_score": self.importance_score,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEvent":
        """从字典创建 MemoryEvent 对象"""
        # 处理 metadata 可能是 JSON 字符串的情况
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}

        return cls(
            memory_content=data["memory_content"],
            importance_score=data["importance_score"],
            session_id=data["session_id"],
            timestamp=data.get("timestamp", time.time()),
            metadata=metadata,
        )

    def is_important(self, threshold: float = 0.5) -> bool:
        """判断记忆是否重要 (高于阈值)"""
        return self.importance_score >= threshold


# 辅助函数


def serialize_to_json(obj: Any) -> str:
    """将对象序列化为 JSON 字符串"""
    if isinstance(obj, list | dict):
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)


def deserialize_from_json(json_str: str | None, default: Any = None) -> Any:
    """从 JSON 字符串反序列化对象"""
    if json_str is None or json_str == "":
        return default if default is not None else {}

    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}
