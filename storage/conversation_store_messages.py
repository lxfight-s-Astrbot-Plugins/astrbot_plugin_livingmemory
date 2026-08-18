"""
ConversationStore 的 ConversationStoreMessagesMixin 拆分模块
自动从 storage/conversation_store.py 拆分，保持行为不变
"""

import json
import time

from astrbot.api import logger
from ..core.models.conversation_models import Message, serialize_to_json


class ConversationStoreMessagesMixin:
    """ConversationStore 拆分模块：ConversationStoreMessagesMixin"""
    async def add_message(self, message: Message) -> int:
        """
        添加消息到数据库

        Args:
            message: 消息对象

        Returns:
            int: 消息ID
        """
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")

        platform = message.platform or "unknown"
        if not isinstance(platform, str):
            platform = getattr(platform, "name", str(platform))
            logger.warning(
                f"[add_message] platform 参数不是字符串类型，已自动转换为: {platform}"
            )

        sender_id = message.sender_id or message.session_id
        content = Message.content_to_text(message.content)
        now = time.time()
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO sessions (
                    session_id, platform, created_at, last_active_at,
                    message_count, participants, metadata
                )
                VALUES (?, ?, ?, ?, 0, '[]', '{}')
                ON CONFLICT(session_id) DO NOTHING
                """,
                (message.session_id, platform, now, message.timestamp),
            )

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
                    content,
                    sender_id,
                    message.sender_name,
                    message.group_id,
                    platform,
                    message.timestamp,
                    serialize_to_json(message.metadata),
                ),
            )

            message_id = cursor.lastrowid if cursor.lastrowid else 0

            await self.connection.execute(
                """
                UPDATE sessions
                SET message_count = message_count + 1,
                    last_active_at = ?,
                    participants = CASE
                        WHEN ? = '' THEN participants
                        WHEN EXISTS (
                            SELECT 1
                            FROM json_each(COALESCE(NULLIF(participants, ''), '[]'))
                            WHERE value = ?
                        ) THEN participants
                        ELSE json_insert(
                            COALESCE(NULLIF(participants, ''), '[]'),
                            '$[#]',
                            ?
                        )
                    END
                WHERE session_id = ?
            """,
                (
                    message.timestamp,
                    sender_id,
                    sender_id,
                    sender_id,
                    message.session_id,
                ),
            )
            await self.connection.commit()

        logger.debug(
            f"[ConversationStore] 添加消息: session={message.session_id}, role={message.role}"
        )
        return message_id

    async def get_messages(
        self, session_id: str, limit: int = 50, sender_id: str | None = None
    ) -> list[Message]:
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

        if self.connection is None:
            return []
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
        if self.connection is None:
            return 0
        async with self.connection.execute(
            """
            SELECT COUNT(*) as count
            FROM messages
            WHERE session_id = ?
        """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and "count" in row.keys():
                count_value = row["count"]
                return int(count_value) if count_value is not None else 0
            return 0

    async def trim_session_messages(
        self,
        session_id: str,
        delete_count: int,
    ) -> int:
        """Delete only summarized oldest messages and refresh the session count."""
        if self.connection is None or delete_count <= 0:
            return 0

        # 整个读-改-写都在写锁内完成，避免与 add_message 交错导致
        # message_count / last_summarized_index 漂移（TOCTOU）。
        async with self._write_lock:
            async with self.connection.execute(
                """
                SELECT
                    s.metadata,
                    COUNT(m.id) AS actual_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.session_id
                WHERE s.session_id = ?
                GROUP BY s.session_id
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return 0

            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            try:
                last_summarized_index = int(
                    metadata.get("last_summarized_index", 0) or 0
                )
            except (TypeError, ValueError):
                last_summarized_index = 0
            last_summarized_index = max(0, last_summarized_index)

            actual_count = int(row["actual_count"] or 0)

            if last_summarized_index > actual_count:
                metadata["last_summarized_index"] = 0
                await self.connection.execute(
                    """
                    UPDATE sessions
                    SET metadata = ?,
                        message_count = ?
                    WHERE session_id = ?
                    """,
                    (
                        json.dumps(metadata, ensure_ascii=False),
                        actual_count,
                        session_id,
                    ),
                )
                await self.connection.commit()
                logger.warning(
                    f"[ConversationStore] 阻止清理未总结消息并重置 last_summarized_index: "
                    f"{session_id} ({last_summarized_index} > {actual_count})"
                )
                return 0

            safe_delete_count = min(delete_count, last_summarized_index)
            if safe_delete_count <= 0:
                return 0

            cursor = await self.connection.execute(
                """
                DELETE FROM messages
                WHERE id IN (
                    SELECT id FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp ASC, id ASC
                    LIMIT ?
                )
                """,
                (session_id, safe_delete_count),
            )
            deleted_count = max(0, cursor.rowcount)
            if deleted_count <= 0:
                return 0

            metadata["last_summarized_index"] = max(
                0, last_summarized_index - deleted_count
            )
            await self.connection.execute(
                """
                UPDATE sessions
                SET message_count = ?,
                    metadata = ?
                WHERE session_id = ?
                """,
                (
                    max(0, actual_count - deleted_count),
                    json.dumps(metadata, ensure_ascii=False),
                    session_id,
                ),
            )
            await self.connection.commit()
        return deleted_count

    async def delete_session_messages(self, session_id: str) -> int:
        """
        删除会话的所有消息

        Args:
            session_id: 会话ID

        Returns:
            int: 删除的消息数量
        """
        if self.connection is None:
            return 0
        async with self._write_lock:
            cursor = await self.connection.execute(
                """
                DELETE FROM messages
                WHERE session_id = ?
            """,
                (session_id,),
            )

            deleted_count = cursor.rowcount

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

    async def get_user_message_stats(self, session_id: str) -> dict[str, int]:
        """
        获取会话中各用户的消息统计 (群聊场景)

        Args:
            session_id: 会话ID

        Returns:
            Dict[str, int]: {sender_id: message_count}
        """
        if self.connection is None:
            return {}
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

    async def update_message_metadata(self, message_id: int, metadata: dict) -> bool:
        """
        更新消息的metadata

        Args:
            message_id: 消息ID
            metadata: 新的metadata字典

        Returns:
            bool: 是否更新成功
        """
        if self.connection is None:
            return False

        try:
            import json

            async with self._write_lock:
                await self.connection.execute(
                    """
                    UPDATE messages
                    SET metadata = ?
                    WHERE id = ?
                    """,
                    (json.dumps(metadata, ensure_ascii=False), message_id),
                )
                await self.connection.commit()
            logger.debug(f"[ConversationStore] 更新消息metadata: id={message_id}")
            return True
        except Exception as e:
            logger.error(f"更新消息metadata失败: {e}", exc_info=True)
            return False

    async def search_messages(
        self, session_id: str, keyword: str, limit: int = 20
    ) -> list[Message]:
        """
        搜索会话中包含关键词的消息

        Args:
            session_id: 会话ID
            keyword: 搜索关键词
            limit: 限制数量

        Returns:
            List[Message]: 匹配的消息列表
        """
        if self.connection is None:
            return []
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

    async def get_messages_range(
        self, session_id: str, offset: int = 0, limit: int = 50
    ) -> list[Message]:
        """
        按范围获取会话消息（使用 SQL OFFSET/LIMIT）

        Args:
            session_id: 会话ID
            offset: 跳过的消息数量（从最旧的开始计算）
            limit: 获取的消息数量

        Returns:
            List[Message]: 消息列表（按时间升序）
        """
        if self.connection is None:
            return []

        # 使用子查询确保按时间升序后再应用 OFFSET/LIMIT
        query = """
            SELECT id, session_id, role, content, sender_id, sender_name,
                   group_id, platform, timestamp, metadata
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ? OFFSET ?
        """

        async with self.connection.execute(
            query, (session_id, limit, offset)
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

        logger.debug(
            f"[get_messages_range] session={session_id}, offset={offset}, "
            f"limit={limit}, 实际获取={len(messages)}条"
        )

        return messages

    async def sync_message_counts(self) -> dict[str, int]:
        """
        同步所有会话的 message_count 与实际消息数量

        用于修复 message_count 不一致的问题（如删除消息后未更新计数）

        Returns:
            Dict[str, int]: {session_id: 修正后的count}
        """
        if self.connection is None:
            return {}

        fixed_sessions = {}

        try:
            async with self._write_lock:
                async with self.connection.execute(
                    """
                    SELECT s.session_id,
                           s.message_count AS recorded_count,
                           COUNT(m.id) AS actual_count
                    FROM sessions s
                    LEFT JOIN messages m ON m.session_id = s.session_id
                    GROUP BY s.session_id
                    HAVING s.message_count != COUNT(m.id)
                    """
                ) as cursor:
                    rows = await cursor.fetchall()

                for row in rows:
                    session_id = row["session_id"]
                    recorded_count = row["recorded_count"]
                    actual_count = int(row["actual_count"] or 0)
                    await self.connection.execute(
                        """
                        UPDATE sessions
                        SET message_count = ?
                        WHERE session_id = ?
                        """,
                        (actual_count, session_id),
                    )
                    fixed_sessions[session_id] = actual_count
                    logger.info(
                        f"[ConversationStore] 修复会话 message_count: "
                        f"{session_id} ({recorded_count} -> {actual_count})"
                    )

                if fixed_sessions:
                    await self.connection.commit()
                    logger.info(
                        f"[ConversationStore] 共修复 {len(fixed_sessions)} 个会话的 message_count"
                    )
                else:
                    logger.info(
                        "[ConversationStore] 所有会话的 message_count 均正确，无需修复"
                    )

            return fixed_sessions

        except Exception as e:
            logger.error(f"同步 message_count 失败: {e}", exc_info=True)
            return {}

    async def reset_summarized_index_if_needed(self, session_id: str) -> bool:
        """
        检查并重置 last_summarized_index（如果它超出实际消息范围）

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否进行了重置
        """
        if self.connection is None:
            return False

        try:
            async with self._write_lock:
                # 获取会话信息
                async with self.connection.execute(
                    "SELECT metadata, message_count FROM sessions WHERE session_id = ?",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if not row:
                    return False

                import json

                metadata_str = row["metadata"] or "{}"
                metadata = json.loads(metadata_str)
                message_count = row["message_count"]

                last_summarized_index = metadata.get("last_summarized_index", 0)

                # 如果 last_summarized_index 超出实际消息数量，重置为0
                if last_summarized_index > message_count:
                    metadata["last_summarized_index"] = 0
                    await self.connection.execute(
                        """
                        UPDATE sessions
                        SET metadata = ?
                        WHERE session_id = ?
                        """,
                        (json.dumps(metadata, ensure_ascii=False), session_id),
                    )
                    await self.connection.commit()
                    logger.warning(
                        f"[ConversationStore] 重置 last_summarized_index: "
                        f"{session_id} ({last_summarized_index} -> 0, 实际消息数={message_count})"
                    )
                    return True

            return False

        except Exception as e:
            logger.error(f"检查 last_summarized_index 失败: {e}", exc_info=True)
            return False
