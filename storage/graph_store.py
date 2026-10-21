"""SQLite-backed graph-memory storage."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .graph_store_write import GraphStoreWriteMixin
from .graph_store_read import GraphStoreReadMixin
from .graph_store_snapshot import GraphStoreSnapshotMixin

class GraphStore(GraphStoreWriteMixin, GraphStoreReadMixin, GraphStoreSnapshotMixin):
    """Persist graph nodes, edges, and searchable entries."""

    _SQLITE_BATCH_SIZE = 500
    _NODE_TOKEN_QUERY_BATCH_SIZE = 200

    def __init__(self, db_path: str, *, cache_reads: bool = False):
        self.db_path = db_path
        # 召回链路只读查询复用同一条连接：每次召回 5～6 次 open/PRAGMA 的开销
        # 在小库上也有毫秒级成本。写路径仍按操作单独连接以隔离事务。
        # 默认关闭，只有长生命周期的 MemoryEngine 实例显式开启；临时 shadow
        # store 与短生命周期测试不需要多占一条连接。
        self._cache_reads = cache_reads
        self._read_conn: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def _connect(self):
        """创建新的SQLite连接并启用WAL模式和busy_timeout。"""
        db = await aiosqlite.connect(self.db_path)
        try:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute("PRAGMA foreign_keys = ON")
            yield db
        finally:
            await db.close()

    @asynccontextmanager
    async def _read_connect(self):
        """复用长期只读连接；未开启缓存时退回按操作建连。"""
        if not self._cache_reads:
            async with self._connect() as db:
                yield db
            return
        if self._read_conn is None:
            db = await aiosqlite.connect(self.db_path)
            await db.execute("PRAGMA busy_timeout = 10000")
            self._read_conn = db
        yield self._read_conn

    async def close(self) -> None:
        """关闭复用的只读连接（插件卸载 / 临时 shadow store 清理时调用）。"""
        if self._read_conn is not None:
            await self._read_conn.close()
            self._read_conn = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_json(payload: dict[str, Any] | None) -> str:
        return json.dumps(payload or {}, ensure_ascii=False)

    @staticmethod
    def _chunked(items: list[int], size: int) -> list[list[int]]:
        return [items[index : index + size] for index in range(0, len(items), size)]

__all__ = ["GraphStore"]
