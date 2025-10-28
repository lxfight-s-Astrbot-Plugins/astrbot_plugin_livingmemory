# -*- coding: utf-8 -*-
"""
会话存储层 - ConversationStore
负责管理会话和消息的持久化存储,使用 SQLite 数据库
"""

import aiosqlite
import time
from pathlib import Path
from typing import List, Optional, Dict
from astrbot.api import logger

from ..core.conversation_models import Message, Session, serialize_to_json


class ConversationStore:
    """
    会话存储管理器

    职责:
    - 管理会话和消息的持久化存储
    - 提供 CRUD 操作接口
    - 支持群聊场景的数据查询
    """

    def __init__(self, db_path: str):
        """
        初始化存储层

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.connection: Optional[aiosqlite.Connection] = None

        # 确保数据库目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """初始化数据库连接并创建表结构"""
        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row

        await self._create_tables()
        await self._create_indexes()

        logger.info(f"[ConversationStore] 数据库初始化完成: {self.db_path}")

    async def close(self) -> None:
        """关闭数据库连接"""
        if self.connection:
            await self.connection.close()
            logger.info("[ConversationStore] 数据库连接已关闭")

    async def _create_tables(self) -> None:
        """创建数据库表结构"""
        # sessions 表 - 会话元数据
        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                platform TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_active_at REAL NOT NULL,
                message_count INTEGER DEFAULT 0,
                participants TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)

        # messages 表 - 消息记录
        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT,
                group_id TEXT,
                platform TEXT,
                timestamp REAL NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)

        await self.connection.commit()

    async def _create_indexes(self) -> None:
        """创建索引以优化查询性能"""
        # sessions 表索引
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_id ON sessions(session_id)"
        )
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_last_active ON sessions(last_active_at DESC)"
        )

        # messages 表索引
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, timestamp DESC)"
        )
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(session_id, sender_id, timestamp DESC)"
        )
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_timestamp ON messages(timestamp DESC)"
        )

        await self.connection.commit()

    # ==================== 会话管理 ====================

    async def create_session(self, session_id: str, platform: str) -> Session:
        """
        创建新会话

        Args:
            session_id: 会话唯一标识
            platform: 平台类型

        Returns:
            Session: 创建的会话对象
        """
        now = time.time()

        cursor = await self.connection.execute(
            """
            INSERT INTO sessions (session_id, platform, created_at, last_active_at, message_count, participants, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (session_id, platform, now, now, 0, "[]", "{}"),
        )

        await self.connection.commit()

        session = Session(
            id=cursor.lastrowid,
            session_id=session_id,
            platform=platform,
            created_at=now,
            last_active_at=now,
            message_count=0,
            participants=[],
            metadata={},
        )

        logger.debug(f"[ConversationStore] 创建会话: {session_id}")
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """
        获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            Optional[Session]: 会话对象,不存在则返回 None
        """
        async with self.connection.execute(
            """
            SELECT id, session_id, platform, created_at, last_active_at,
                   message_count, participants, metadata
            FROM sessions
            WHERE session_id = ?
        """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        return Session.from_dict(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "platform": row["platform"],
                "created_at": row["created_at"],
                "last_active_at": row["last_active_at"],
                "message_count": row["message_count"],
                "participants": row["participants"],
                "metadata": row["metadata"],
            }
        )

    async def update_session_activity(self, session_id: str) -> None:
        """
        更新会话最后活跃时间

        Args:
            session_id: 会话ID
        """
        now = time.time()

        await self.connection.execute(
            """
            UPDATE sessions
            SET last_active_at = ?
            WHERE session_id = ?
        """,
            (now, session_id),
        )

        await self.connection.commit()

    async def get_recent_sessions(self, limit: int = 10) -> List[Session]:
        """
        获取最近活跃的会话

        Args:
            limit: 返回数量限制

        Returns:
            List[Session]: 会话列表
        """
        async with self.connection.execute(
            """
            SELECT id, session_id, platform, created_at, last_active_at,
                   message_count, participants, metadata
            FROM sessions
            ORDER BY last_active_at DESC
            LIMIT ?
        """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()

        sessions = []
        for row in rows:
            sessions.append(
                Session.from_dict(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "platform": row["platform"],
                        "created_at": row["created_at"],
                        "last_active_at": row["last_active_at"],
                        "message_count": row["message_count"],
                        "participants": row["participants"],
                        "metadata": row["metadata"],
                    }
                )
            )

        return sessions

    async def delete_old_sessions(self, days: int = 30) -> int:
        """
        删除过期会话及其消息

        Args:
            days: 天数阈值,删除超过此天数未活跃的会话

        Returns:
            int: 删除的会话数量
        """
        cutoff_time = time.time() - (days * 24 * 60 * 60)

        # 获取要删除的会话ID列表
        async with self.connection.execute(
            """
            SELECT session_id FROM sessions
            WHERE last_active_at < ?
        """,
            (cutoff_time,),
        ) as cursor:
            rows = await cursor.fetchall()
            session_ids = [row["session_id"] for row in rows]

        if not session_ids:
            return 0

        # 删除这些会话的所有消息
        placeholders = ",".join("?" * len(session_ids))
        await self.connection.execute(
            f"DELETE FROM messages WHERE session_id IN ({placeholders})", session_ids
        )

        # 删除会话记录
        await self.connection.execute(
            f"DELETE FROM sessions WHERE session_id IN ({placeholders})", session_ids
        )

        await self.connection.commit()

        logger.info(
            f"[ConversationStore] 删除了 {len(session_ids)} 个过期会话 (超过 {days} 天)"
        )
        return len(session_ids)

    async def get_session_participants(self, session_id: str) -> List[str]:
        """
        获取会话参与者列表 (群聊场景)

        Args:
            session_id: 会话ID

        Returns:
            List[str]: 参与者ID列表
        """
        session = await self.get_session(session_id)
        if session:
            return session.participants
        return []

    async def add_session_participant(self, session_id: str, sender_id: str) -> None:
        """
        添加会话参与者 (避免重复)

        Args:
            session_id: 会话ID
            sender_id: 发送者ID
        """
        session = await self.get_session(session_id)
        if not session:
            return

        if sender_id not in session.participants:
            session.participants.append(sender_id)

            await self.connection.execute(
                """
                UPDATE sessions
                SET participants = ?
                WHERE session_id = ?
            """,
                (serialize_to_json(session.participants), session_id),
            )

            await self.connection.commit()

    # ==================== 消息管理 ====================

    async def add_message(self, message: Message) -> int:
        """
        添加消息到数据库

        Args:
            message: 消息对象

        Returns:
            int: 消息ID
        """
        # 确保会话存在
        session = await self.get_session(message.session_id)
        if not session:
            # 自动创建会话
            platform = message.platform or "unknown"
            session = await self.create_session(message.session_id, platform)

        # 插入消息
        cursor = await self.connection.execute(
            """
            INSERT INTO messages (
                session_id, role, content, sender_id, sender_name,
                group_id, platform, timestamp, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                message.session_id,
                message.role,
                message.content,
                message.sender_id,
                message.sender_name,
                message.group_id,
                message.platform,
                message.timestamp,
                serialize_to_json(message.metadata),
            ),
        )

        message_id = cursor.lastrowid

        # 更新会话统计
        await self.connection.execute(
            """
            UPDATE sessions
            SET message_count = message_count + 1,
                last_active_at = ?
            WHERE session_id = ?
        """,
            (message.timestamp, message.session_id),
        )

        # 添加参与者
        if message.sender_id:
            await self.add_session_participant(message.session_id, message.sender_id)

        await self.connection.commit()

        logger.debug(
            f"[ConversationStore] 添加消息: session={message.session_id}, role={message.role}"
        )
        return message_id

    async def get_messages(
        self, session_id: str, limit: int = 50, sender_id: Optional[str] = None
    ) -> List[Message]:
        """
        获取会话消息 (支持按发送者过滤)

        Args:
            session_id: 会话ID
            limit: 限制数量
            sender_id: 可选,按发送者ID过滤

        Returns:
            List[Message]: 消息列表 (按时间升序)
        """
        if sender_id:
            # 按发送者过滤
            query = """
                SELECT id, session_id, role, content, sender_id, sender_name,
                       group_id, platform, timestamp, metadata
                FROM messages
                WHERE session_id = ? AND sender_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (session_id, sender_id, limit)
        else:
            # 获取所有消息
            query = """
                SELECT id, session_id, role, content, sender_id, sender_name,
                       group_id, platform, timestamp, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (session_id, limit)

        async with self.connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            messages.append(
                Message.from_dict(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "group_id": row["group_id"],
                        "platform": row["platform"],
                        "timestamp": row["timestamp"],
                        "metadata": row["metadata"],
                    }
                )
            )

        # 反转列表,返回时间升序
        messages.reverse()
        return messages

    async def get_message_count(self, session_id: str) -> int:
        """
        获取会话的消息总数

        Args:
            session_id: 会话ID

        Returns:
            int: 消息数量
        """
        async with self.connection.execute(
            """
            SELECT COUNT(*) as count
            FROM messages
            WHERE session_id = ?
        """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row["count"] if row else 0

    async def delete_session_messages(self, session_id: str) -> int:
        """
        删除会话的所有消息

        Args:
            session_id: 会话ID

        Returns:
            int: 删除的消息数量
        """
        cursor = await self.connection.execute(
            """
            DELETE FROM messages
            WHERE session_id = ?
        """,
            (session_id,),
        )

        deleted_count = cursor.rowcount

        # 重置会话的消息计数
        await self.connection.execute(
            """
            UPDATE sessions
            SET message_count = 0
            WHERE session_id = ?
        """,
            (session_id,),
        )

        await self.connection.commit()

        logger.info(
            f"[ConversationStore] 删除会话消息: session={session_id}, count={deleted_count}"
        )
        return deleted_count

    # ==================== 高级查询 ====================

    async def get_user_message_stats(self, session_id: str) -> Dict[str, int]:
        """
        获取会话中各用户的消息统计 (群聊场景)

        Args:
            session_id: 会话ID

        Returns:
            Dict[str, int]: {sender_id: message_count}
        """
        async with self.connection.execute(
            """
            SELECT sender_id, COUNT(*) as count
            FROM messages
            WHERE session_id = ? AND role = 'user'
            GROUP BY sender_id
        """,
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        stats = {}
        for row in rows:
            stats[row["sender_id"]] = row["count"]

        return stats

    async def search_messages(
        self, session_id: str, keyword: str, limit: int = 20
    ) -> List[Message]:
        """
        搜索会话中包含关键词的消息

        Args:
            session_id: 会话ID
            keyword: 搜索关键词
            limit: 限制数量

        Returns:
            List[Message]: 匹配的消息列表
        """
        async with self.connection.execute(
            """
            SELECT id, session_id, role, content, sender_id, sender_name,
                   group_id, platform, timestamp, metadata
            FROM messages
            WHERE session_id = ? AND content LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (session_id, f"%{keyword}%", limit),
        ) as cursor:
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            messages.append(
                Message.from_dict(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "group_id": row["group_id"],
                        "platform": row["platform"],
                        "timestamp": row["timestamp"],
                        "metadata": row["metadata"],
                    }
                )
            )

        return messages
