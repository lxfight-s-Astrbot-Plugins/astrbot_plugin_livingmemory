"""
会话存储层 - ConversationStore
负责管理会话和消息的持久化存储,使用 SQLite 数据库
"""

import asyncio
import json
import time
from pathlib import Path

import aiosqlite

from astrbot.api import logger

from ..core.models.conversation_models import Session, serialize_to_json
from .conversation_store_messages import ConversationStoreMessagesMixin

class ConversationStore(ConversationStoreMessagesMixin):
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
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

        # 确保数据库目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """初始化数据库连接并创建表结构"""
        self.connection = await aiosqlite.connect(self.db_path)
        if self.connection is not None:
            self.connection.row_factory = aiosqlite.Row
            await self.connection.execute("PRAGMA journal_mode = WAL")
            await self.connection.execute("PRAGMA busy_timeout = 10000")

        await self._create_tables()
        await self._create_indexes()

        logger.info(f"[ConversationStore] 数据库初始化完成: {self.db_path}")

    async def close(self) -> None:
        """关闭数据库连接"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info("[ConversationStore] 数据库连接已关闭")

    async def _create_tables(self) -> None:
        """创建数据库表结构"""
        # sessions 表 - 会话元数据
        if self.connection is not None:
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
        if self.connection is not None:
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
            await self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_session_id ON messages(session_id, id)"
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

        # 确保 platform 是字符串类型
        if not isinstance(platform, str):
            # 如果是 PlatformMetadata 对象，提取 name 属性
            platform = getattr(platform, "name", str(platform))
            logger.warning(
                f"[create_session] platform 参数不是字符串类型，已自动转换为: {platform}"
            )

        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO sessions (session_id, platform, created_at, last_active_at, message_count, participants, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
            """,
                (session_id, platform, now, now, 0, "[]", "{}"),
            )
            await self.connection.commit()

        session = await self.get_session(session_id)
        if session is not None:
            logger.debug(f"[ConversationStore] 会话已存在: {session_id}")
            return session

        session = Session(
            id=0,
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

    async def get_session(self, session_id: str) -> Session | None:
        """
        获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            Optional[Session]: 会话对象,不存在则返回 None
        """
        if self.connection is None:
            return None
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

        if self.connection is None:
            return
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE sessions
                SET last_active_at = ?
                WHERE session_id = ?
            """,
                (now, session_id),
            )
            await self.connection.commit()

    async def get_recent_sessions(self, limit: int = 10) -> list[Session]:
        """
        获取最近活跃的会话

        Args:
            limit: 返回数量限制

        Returns:
            List[Session]: 会话列表
        """
        if self.connection is None:
            return []
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

    async def delete_old_sessions(
        self, days: int = 30, ttl_seconds: int | None = None
    ) -> int:
        """
        删除过期会话及其消息

        Args:
            days: 天数阈值（兼容旧调用）
            ttl_seconds: 秒级TTL阈值（优先使用）

        Returns:
            int: 删除的会话数量
        """
        effective_ttl_seconds = (
            int(ttl_seconds) if ttl_seconds is not None else int(days * 24 * 60 * 60)
        )
        if effective_ttl_seconds <= 0:
            effective_ttl_seconds = 60
        cutoff_time = time.time() - effective_ttl_seconds

        if self.connection is None:
            return 0
        async with self._write_lock:
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
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                session_ids,
            )

            # 删除会话记录
            await self.connection.execute(
                f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
                session_ids,
            )

            await self.connection.commit()

        logger.info(
            f"[ConversationStore] 删除了 {len(session_ids)} 个过期会话 "
            f"(超过 {effective_ttl_seconds} 秒)"
        )
        return len(session_ids)

    async def get_session_participants(self, session_id: str) -> list[str]:
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
        if self.connection is None:
            return

        async with self._write_lock:
            async with self.connection.execute(
                "SELECT participants FROM sessions WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return

            try:
                participants = json.loads(row["participants"] or "[]")
            except (json.JSONDecodeError, TypeError):
                participants = []
            if not isinstance(participants, list):
                participants = []

            if sender_id in participants:
                return

            participants.append(sender_id)
            await self.connection.execute(
                """
                UPDATE sessions
                SET participants = ?
                WHERE session_id = ?
            """,
                (serialize_to_json(participants), session_id),
            )

            await self.connection.commit()

    # ==================== 消息管理 ====================

    # ==================== 高级查询 ====================
