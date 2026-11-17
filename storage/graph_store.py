"""SQLite-backed graph-memory storage."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from astrbot.api import logger

from .graph_store_read import GraphStoreReadMixin
from .graph_store_write import GraphStoreWriteMixin
from .schema import GRAPH_SCHEMA_STATEMENTS
from .sqlite_connection import open_connection

# 节点检索的 bigram 索引把 CJK 连续段展开成“单字 + 相邻二字组”；
# 中文查询里的 2 字词因此能一次 FTS MATCH 命中，不再全表 LIKE。
_CJK_RUN = re.compile(r"[一-鿿]+")


class GraphStore(GraphStoreWriteMixin, GraphStoreReadMixin):
    """Persist graph nodes, edges, and searchable entries."""

    _SQLITE_BATCH_SIZE = 500
    _NODE_TOKEN_QUERY_BATCH_SIZE = 200
    # 图入口 FTS 的索引格式版本（PRAGMA user_version）：1 = 存分词后的词元。
    _ENTRY_FTS_TOKENIZED_VERSION = 1

    # initialize() 中赋值：trigram tokenizer 不可用时保持 False，供节点检索选择回退路径
    _node_fts_available: bool

    def __init__(self, db_path: str, *, cache_reads: bool = False):
        self.db_path = db_path
        # 召回链路只读查询复用同一条连接：每次召回 5～6 次 open/PRAGMA 的开销
        # 在小库上也有毫秒级成本。写路径仍按操作单独连接以隔离事务。
        # 默认关闭，只有长生命周期的 MemoryEngine 实例显式开启；临时 shadow
        # store 与短生命周期测试不需要多占一条连接。
        self._cache_reads = cache_reads
        self._read_conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create tables used by the graph-memory layer."""
        async with self._connect() as db:
            for statement in GRAPH_SCHEMA_STATEMENTS:
                await db.execute(statement)
            # External-content triggers also cover graph snapshot replacement.
            existing = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'livingmemory_graph_nodes_fts'"
            )
            needs_backfill = await existing.fetchone() is None
            try:
                await db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_graph_nodes_fts
                    USING fts5(canonical_value, content='graph_nodes', content_rowid='id', tokenize='trigram')
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS lmem_nodes_insert AFTER INSERT ON graph_nodes BEGIN
                        INSERT INTO livingmemory_graph_nodes_fts(rowid, canonical_value)
                        VALUES (new.id, new.canonical_value);
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS lmem_nodes_delete AFTER DELETE ON graph_nodes BEGIN
                        INSERT INTO livingmemory_graph_nodes_fts(livingmemory_graph_nodes_fts, rowid, canonical_value)
                        VALUES ('delete', old.id, old.canonical_value);
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS lmem_nodes_update AFTER UPDATE OF canonical_value ON graph_nodes BEGIN
                        INSERT INTO livingmemory_graph_nodes_fts(livingmemory_graph_nodes_fts, rowid, canonical_value)
                        VALUES ('delete', old.id, old.canonical_value);
                        INSERT INTO livingmemory_graph_nodes_fts(rowid, canonical_value)
                        VALUES (new.id, new.canonical_value);
                    END
                """)
                if needs_backfill:
                    await db.execute(
                        "INSERT INTO livingmemory_graph_nodes_fts(livingmemory_graph_nodes_fts) VALUES ('rebuild')"
                    )
                self._node_fts_available = True
            except aiosqlite.OperationalError:
                self._node_fts_available = False
                logger.warning(
                    "SQLite trigram tokenizer unavailable; using node substring search"
                )
            # trigram 索引只覆盖 >=3 字的中文匹配；2 字词单独建 bigram FTS，
            # 节点写入时同步维护，缺失行（旧库/升级）在这里一次性回填。
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_graph_nodes_bigram_fts
                USING fts5(tokenized, tokenize='unicode61')
            """)
            cursor = await db.execute(
                "SELECT (SELECT COUNT(*) FROM graph_nodes), "
                "(SELECT COUNT(*) FROM livingmemory_graph_nodes_bigram_fts)"
            )
            node_count, bigram_count = await cursor.fetchone()
            if node_count > bigram_count:
                await db.execute("DELETE FROM livingmemory_graph_nodes_bigram_fts")
                cursor = await db.execute("SELECT id, canonical_value FROM graph_nodes")
                await db.executemany(
                    "INSERT INTO livingmemory_graph_nodes_bigram_fts(rowid, tokenized) "
                    "VALUES (?, ?)",
                    [
                        (int(node_id), self._tokenize_cjk_text(canonical_value))
                        for node_id, canonical_value in await cursor.fetchall()
                    ],
                )
            # 入口 FTS 存分词后的词元（CJK 单字 + 相邻二字组），但旧库存的是未分词
            # 原文，unicode61 会把整句中文当成一个词元，中文关键词无法命中。用
            # user_version 记录索引格式版本，首次升级按 graph_entries.content 回填。
            cursor = await db.execute("PRAGMA user_version")
            (entry_index_version,) = await cursor.fetchone()
            if int(entry_index_version or 0) < self._ENTRY_FTS_TOKENIZED_VERSION:
                await db.execute("DELETE FROM livingmemory_graph_entries_fts")
                last_entry_id = 0
                while True:
                    cursor = await db.execute(
                        "SELECT id, content FROM graph_entries "
                        "WHERE id > ? ORDER BY id LIMIT ?",
                        (last_entry_id, self._SQLITE_BATCH_SIZE),
                    )
                    rows = await cursor.fetchall()
                    if not rows:
                        break
                    await db.executemany(
                        "INSERT INTO livingmemory_graph_entries_fts(entry_id, content) "
                        "VALUES (?, ?)",
                        [
                            (int(entry_id), self._tokenize_cjk_text(content))
                            for entry_id, content in rows
                        ],
                    )
                    last_entry_id = int(rows[-1][0])
                await db.execute(
                    f"PRAGMA user_version = {self._ENTRY_FTS_TOKENIZED_VERSION}"
                )
                if last_entry_id:
                    logger.info(
                        f"[GraphStore] 已按分词格式回填 {last_entry_id} 条图入口 FTS"
                    )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_edges_semantic
                ON graph_edges(source_node_id, target_node_id, relation_type)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_entries_scope_latest
                ON graph_entries(session_id, persona_id, source_memory_id, id DESC)
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_session_id ON graph_entries(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_persona_id ON graph_entries(persona_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entry_nodes_node ON graph_entry_nodes(node_id)"
            )
            await db.commit()

    @asynccontextmanager
    async def _connect(self):
        """创建新的SQLite连接（busy_timeout 与 journal 策略由连接层统一设置）。"""
        db = await open_connection(self.db_path)
        try:
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
            self._read_conn = await open_connection(self.db_path)
        yield self._read_conn

    async def close(self) -> None:
        """关闭复用的只读连接（插件卸载 / 临时 shadow store 清理时调用）。"""
        if self._read_conn is not None:
            await self._read_conn.close()
            self._read_conn = None

    @staticmethod
    def _tokenize_cjk_text(value: Any) -> str:
        """Build the FTS payload shared by node values and entry content.

        CJK runs are expanded into single characters plus adjacent bigrams so a
        jieba-tokenized query can match them; other text is left as-is for the
        unicode61 tokenizer to split.
        """
        text = str(value or "")
        tokens: list[str] = []
        for match in _CJK_RUN.finditer(text):
            run = match.group(0)
            tokens.extend(run)
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        for part in _CJK_RUN.split(text):
            part = part.strip()
            if part:
                tokens.append(part)
        return " ".join(dict.fromkeys(tokens))

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
