# -*- coding: utf-8 -*-

import json
import aiosqlite
from typing import List, Dict, Any, Optional

from ..core.models.memory_models import Memory


class MemoryStorage:
    """
    用于在 SQLite 中持久化、检索和管理结构化 Memory 对象的类。
    """

    def __init__(self, connection: aiosqlite.Connection):
        """
        修正: 接收一个已建立的 aiosqlite 连接
        """
        self.connection = connection

    async def initialize_schema(self):
        """
        建立数据库连接并创建表
        """
        # 修正: 创建包含所有需要字段的表
        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                importance_score REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                community_id TEXT,  -- 为社区发现预留字段
                memory_data TEXT NOT NULL
            )
        """)
        await self.connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_id ON memories (memory_id);
        """)
        await self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_status ON memories (status);
        """)
        await self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_community ON memories (community_id);
        """)
        await self.connection.commit()

    # Note: 不提供 close() 方法，因为这个类不负责连接的生命周期管理
    # 连接的创建和关闭应由更高层的组件（如 FaissManagerV2）负责

    async def add_memory(self, memory: Memory) -> int:
        """
        将一个 Memory 对象添加到数据库，并返回其内部自增 ID。
        """
        memory_json = json.dumps(memory.to_dict(), ensure_ascii=False)
        status = "active"  # 默认状态

        cursor = await self.connection.execute(
            """
            INSERT INTO memories (memory_id, timestamp, memory_type, importance_score, status, memory_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.timestamp,
                memory.metadata.memory_type,
                memory.metadata.importance_score,
                status,
                memory_json,
            ),
        )
        await self.connection.commit()
        return cursor.lastrowid

    async def get_memories_by_internal_ids(
        self, internal_ids: List[int]
    ) -> List[Dict[str, Any]]:
        """
        通过内部自增 ID 列表获取记忆数据。
        """
        if not internal_ids:
            return []
        # 安全的 SQL 构造: 使用固定模板而非 f-string
        placeholders = ",".join("?" * len(internal_ids))
        sql = "SELECT id, memory_id, memory_data FROM memories WHERE id IN ({})".format(
            placeholders
        )
        async with self.connection.execute(sql, internal_ids) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_memories_by_memory_ids(
        self, memory_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        通过全局 memory_id (UUID) 列表获取记忆数据。
        """
        if not memory_ids:
            return []
        # 安全的 SQL 构造: 使用固定模板而非 f-string
        placeholders = ",".join("?" * len(memory_ids))
        sql = "SELECT id, memory_id, memory_data FROM memories WHERE memory_id IN ({})".format(
            placeholders
        )
        async with self.connection.execute(sql, memory_ids) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_all_memories(self) -> List[Dict[str, Any]]:
        """
        获取数据库中所有的记忆。
        """
        async with self.connection.execute(
            "SELECT id, memory_id, memory_data FROM memories"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_memories(self, memories_to_update: List[Dict[str, Any]]):
        """
        根据 memory_id 批量更新 memory_data。
        """
        if not memories_to_update:
            return
        updates = [(mem["memory_data"], mem["memory_id"]) for mem in memories_to_update]
        await self.connection.executemany(
            "UPDATE memories SET memory_data = ? WHERE memory_id = ?", updates
        )
        await self.connection.commit()

    async def delete_memories_by_internal_ids(self, internal_ids: List[int]):
        """
        根据内部 ID 列表删除记忆。
        """
        if not internal_ids:
            return
        # 安全的 SQL 构造: 使用固定模板而非 f-string
        placeholders = ",".join("?" * len(internal_ids))
        sql = "DELETE FROM memories WHERE id IN ({})".format(placeholders)
        await self.connection.execute(sql, internal_ids)
        await self.connection.commit()

    async def update_memory_status(self, internal_ids: List[int], new_status: str):
        """
        批量更新记忆的状态 (例如, 改为 'archived')
        """
        if not internal_ids:
            return
        # 安全的 SQL 构造: 使用固定模板而非 f-string
        placeholders = ",".join("?" * len(internal_ids))
        sql = "UPDATE memories SET status = ? WHERE id IN ({})".format(placeholders)
        # 注意参数绑定的方式，new_status 在前
        await self.connection.execute(sql, [new_status] + internal_ids)
        await self.connection.commit()

    async def count_memories(
        self,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> int:
        """统计记忆数量，可选状态与关键字过滤。"""
        sql = "SELECT COUNT(*) FROM memories"
        conditions = []
        params: List[Any] = []

        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        if keyword:
            conditions.append("(memory_data LIKE ? OR memory_id LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        async with self.connection.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def get_memories_paginated(
        self,
        page_size: int,
        offset: int = 0,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """分页获取记忆数据，同时支持状态和关键字筛选。"""
        sql = """
            SELECT id, memory_id, timestamp, memory_type, importance_score, status, community_id, memory_data
            FROM memories
        """
        conditions = []
        params: List[Any] = []

        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        if keyword:
            conditions.append("(memory_data LIKE ? OR memory_id LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY datetime(timestamp) DESC, id DESC"

        if page_size > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([page_size, offset])

        async with self.connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        memories: List[Dict[str, Any]] = []
        for row in rows:
            memories.append(
                {
                    "id": row[0],
                    "memory_id": row[1],
                    "timestamp": row[2],
                    "memory_type": row[3],
                    "importance_score": row[4],
                    "status": row[5],
                    "community_id": row[6],
                    "memory_data": row[7],
                }
            )
        return memories

    async def get_memory_by_memory_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """根据 memory_id 获取单条记忆。"""
        async with self.connection.execute(
            """
            SELECT id, memory_id, timestamp, memory_type, importance_score, status, community_id, memory_data
            FROM memories
            WHERE memory_id = ?
            """,
            (memory_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "id": row[0],
            "memory_id": row[1],
            "timestamp": row[2],
            "memory_type": row[3],
            "importance_score": row[4],
            "status": row[5],
            "community_id": row[6],
            "memory_data": row[7],
        }

    async def delete_memories_by_memory_ids(self, memory_ids: List[str]):
        """根据 memory_id 列表删除记忆。"""
        if not memory_ids:
            return

        # 安全的 SQL 构造: 使用固定模板而非 f-string
        placeholders = ",".join("?" * len(memory_ids))
        sql = "DELETE FROM memories WHERE memory_id IN ({})".format(placeholders)
        await self.connection.execute(sql, memory_ids)
        await self.connection.commit()

    async def delete_all_memories(self) -> int:
        """删除所有结构化记忆记录，返回删除数量。"""
        cursor = await self.connection.execute("DELETE FROM memories")
        await self.connection.commit()
        return cursor.rowcount if cursor and cursor.rowcount is not None else 0
