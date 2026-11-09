"""SQLite-backed storage for time-aware memory atoms."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite

from astrbot.api import logger

from ..core.memory_scope import adapter_sql
from ..core.models.memory_atom import (
    AtomStatus,
    AtomType,
    DecayType,
    MemoryAtom,
    compute_ttl,
)
from ..core.utils.json_utils import safe_json_dict
from .sqlite_connection import open_connection

if TYPE_CHECKING:
    from ..core.processors.text_processor import TextProcessor


class AtomStore:
    """Persist memory atoms with FTS search support."""

    _SQLITE_BATCH_SIZE = 500
    # FTS 内容格式：raw=源文本原样（unicode61 会把连续中文当成单 token），
    # tokenized=与查询共用 TextProcessor 分词后的空格分隔文本。
    _FTS_FORMAT_RAW = "raw"
    _FTS_FORMAT_TOKENIZED = "tokenized"
    _FTS_STATE_TABLE = "atom_store_fts_state"

    def __init__(
        self,
        db_path: str,
        *,
        cache_reads: bool = False,
        text_processor: "TextProcessor | None" = None,
    ):
        self.db_path = db_path
        # 召回链路每次查询都建连 + PRAGMA 会在并发下被 WAL 锁串行化；长生命
        # 周期实例（MemoryEngine）复用一条只读连接，写路径仍按操作独立建连。
        self._cache_reads = cache_reads
        # 写入与查询共用同一分词器，中文整句才能命中分词后的原子内容。
        self._text_processor = text_processor
        self._read_conn: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def _connect(self):
        db = await open_connection(self.db_path)
        try:
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
        """关闭复用的只读连接（插件卸载 / 重新初始化前调用，幂等）。"""
        if self._read_conn is not None:
            await self._read_conn.close()
            self._read_conn = None

    def _fts_format(self) -> str:
        return (
            self._FTS_FORMAT_TOKENIZED
            if self._text_processor is not None
            else self._FTS_FORMAT_RAW
        )

    async def _fts_content(self, content: str) -> str:
        """Tokenize text for the FTS index with the query tokenizer."""
        if self._text_processor is None:
            return content
        tokens = await self._text_processor.tokenize_async(
            content, remove_stopwords=True
        )
        return " ".join(tokens)

    async def _query_tokens(self, query: str) -> list[str]:
        """Tokenize a search query exactly like indexed atom content."""
        if self._text_processor is None:
            return [token for token in query.strip().split() if token]
        return await self._text_processor.tokenize_async(query, remove_stopwords=True)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_json(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        return json.dumps(payload if payload is not None else {}, ensure_ascii=False)

    async def initialize(self) -> None:
        """Create tables for memory atoms."""
        async with self._connect() as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_atoms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_memory_id INTEGER NOT NULL,
                    atom_type TEXT NOT NULL DEFAULT 'unknown',
                    content TEXT NOT NULL,
                    entities TEXT DEFAULT '[]',
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    last_reinforced_at REAL,
                    event_time REAL,
                    ttl_days REAL NOT NULL DEFAULT 30.0,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    reinforcement_count INTEGER NOT NULL DEFAULT 0,
                    decay_type TEXT NOT NULL DEFAULT 'exponential',
                    session_id TEXT,
                    persona_id TEXT,
                    metadata TEXT DEFAULT '{}'
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_parent ON memory_atoms(parent_memory_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_status ON memory_atoms(status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_expires ON memory_atoms(expires_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_session ON memory_atoms(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_persona ON memory_atoms(persona_id)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_atoms_scope_status
                ON memory_atoms(status, session_id, persona_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_atoms_status_expires
                ON memory_atoms(status, expires_at)
                """
            )
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_atoms_fts
                USING fts5(content, atom_id UNINDEXED, tokenize='unicode61')
                """
            )
            await db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._FTS_STATE_TABLE} (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            # FTS 表是后加能力：旧库存量原子没有对应 FTS 行，且历史格式是
            # 源文本原样写入。这里按当前分词格式与「非 forgotten」原子对账。
            await self._sync_fts_index(db)
            await db.commit()

    async def _sync_fts_index(self, db: aiosqlite.Connection) -> None:
        """Rebuild or repair the atom FTS index without resurrecting forgotten atoms.

        Args:
            db: Connection opened by ``initialize()``.
        """
        expected_format = self._fts_format()
        cursor = await db.execute(
            f"SELECT value FROM {self._FTS_STATE_TABLE} WHERE key = 'fts_format'"
        )
        row = await cursor.fetchone()
        stored_format = row[0] if row else None
        # forgotten 原子会被 forget_expired_atoms 有意移出 FTS，不能计入期望行数，
        # 否则每次启动都会误判不一致并全量重建（并把 forgotten 原子写回索引）。
        cursor = await db.execute(
            "SELECT COUNT(*) FROM memory_atoms WHERE status != ?",
            (AtomStatus.FORGOTTEN.value,),
        )
        expected_count = int((await cursor.fetchone())[0])

        if stored_format == expected_format:
            cursor = await db.execute("SELECT COUNT(*) FROM memory_atoms_fts")
            fts_count = int((await cursor.fetchone())[0])
            if fts_count == expected_count:
                return
            logger.info(
                f"[AtomStore] 原子 FTS 索引不一致（{fts_count}/{expected_count}），"
                "已按源表回填"
            )
        await self._rebuild_fts(db)
        await db.execute(
            f"INSERT OR REPLACE INTO {self._FTS_STATE_TABLE}(key, value) "
            "VALUES ('fts_format', ?)",
            (expected_format,),
        )

    async def _rebuild_fts(self, db: aiosqlite.Connection) -> None:
        """Rewrite FTS rows for all non-forgotten atoms in the current format."""
        await db.execute("DELETE FROM memory_atoms_fts")
        cursor = await db.execute(
            "SELECT id, content FROM memory_atoms WHERE status != ? ORDER BY id",
            (AtomStatus.FORGOTTEN.value,),
        )
        rows = await cursor.fetchall()
        batch: list[tuple[int, str]] = []
        for atom_id, content in rows:
            batch.append((int(atom_id), await self._fts_content(str(content))))
            if len(batch) >= self._SQLITE_BATCH_SIZE:
                await db.executemany(
                    "INSERT INTO memory_atoms_fts(atom_id, content) VALUES (?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            await db.executemany(
                "INSERT INTO memory_atoms_fts(atom_id, content) VALUES (?, ?)", batch
            )

    async def insert(self, atom: MemoryAtom) -> int:
        """Insert a new atom and return its id. Updates atom.atom_id in place."""
        self._prepare_atom_for_insert(atom)

        async with self._connect() as db:
            atom_id = await self._insert_atom(db, atom)
            await db.commit()
        return atom_id

    async def insert_many(self, atoms: list[MemoryAtom]) -> list[int]:
        """Insert atoms in chunked transactions and return their ids."""
        if not atoms:
            return []

        atom_ids: list[int] = []
        async with self._connect() as db:
            for index in range(0, len(atoms), self._SQLITE_BATCH_SIZE):
                batch = atoms[index : index + self._SQLITE_BATCH_SIZE]
                batch_atom_ids: list[int] = []
                prepared_batch: list[MemoryAtom] = []
                try:
                    for atom in batch:
                        self._prepare_atom_for_insert(atom)
                        prepared_batch.append(atom)
                        batch_atom_ids.append(await self._insert_atom(db, atom))
                    await db.commit()
                except Exception:
                    await db.rollback()
                    for atom in prepared_batch:
                        atom.atom_id = 0
                    raise
                atom_ids.extend(batch_atom_ids)
        return atom_ids

    def _prepare_atom_for_insert(self, atom: MemoryAtom) -> None:
        """Populate time-derived fields before persistence."""
        now = time.time()
        atom.created_at = now
        atom.last_accessed_at = now
        ttl, decay = compute_ttl(
            atom.atom_type, atom.importance, atom.reinforcement_count, atom.event_time
        )
        atom.ttl_days = ttl
        atom.decay_type = decay
        atom.expires_at = now + ttl * 86400.0

    async def _insert_atom(
        self,
        db: aiosqlite.Connection,
        atom: MemoryAtom,
    ) -> int:
        cursor = await db.execute(
            """
            INSERT INTO memory_atoms (
                parent_memory_id, atom_type, content, entities,
                importance, confidence, created_at, last_accessed_at,
                last_reinforced_at, event_time, ttl_days, expires_at,
                status, reinforcement_count, decay_type,
                session_id, persona_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atom.parent_memory_id,
                atom.atom_type.value,
                atom.content,
                json.dumps(atom.entities, ensure_ascii=False),
                atom.importance,
                atom.confidence,
                atom.created_at,
                atom.last_accessed_at,
                atom.last_reinforced_at,
                atom.event_time,
                atom.ttl_days,
                atom.expires_at,
                atom.status.value,
                atom.reinforcement_count,
                atom.decay_type.value,
                atom.session_id,
                atom.persona_id,
                self._to_json(atom.metadata),
            ),
        )
        atom_id = int(cursor.lastrowid)
        atom.atom_id = atom_id

        await db.execute(
            "INSERT INTO memory_atoms_fts(atom_id, content) VALUES (?, ?)",
            (atom_id, await self._fts_content(atom.content)),
        )
        return atom_id

    async def get(self, atom_id: int) -> MemoryAtom | None:
        """Retrieve a single atom by id."""
        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memory_atoms WHERE id = ?", (atom_id,)
            )
            row = await cursor.fetchone()
        return self._row_to_atom(row) if row else None

    async def get_by_parent(self, parent_memory_id: int) -> list[MemoryAtom]:
        """Retrieve all atoms belonging to a parent memory document."""
        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memory_atoms WHERE parent_memory_id = ? ORDER BY id ASC",
                (parent_memory_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_atom(row) for row in rows]

    async def search_fts(
        self,
        query: str,
        limit: int = 20,
        session_id: str | None = None,
        persona_id: str | None = None,
        include_expired: bool = False,
    ) -> list[MemoryAtom]:
        """Full-text search over atom content, returning time-scored results."""
        if not query or not query.strip():
            return []

        # 与写入侧共用同一分词器：中文整句必须先切成词，否则 unicode61
        # 会把整段中文当成一个 token，用户查询永远匹配不到。
        tokens = await self._query_tokens(query)
        if not tokens:
            return []
        escaped = [token.replace('"', '""') for token in tokens]
        fts_query = " OR ".join(f'"{token}"' for token in escaped)

        filters = (
            ["ma.status = 'active'", "ma.expires_at > ?"] if not include_expired else []
        )
        params: list[Any] = [fts_query]
        if not include_expired:
            params.append(time.time())
        if session_id is not None:
            filters.append("ma.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ma.persona_id = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("ma.metadata")
        filters.append(ownership)
        params.extend(ownership_params)

        where_clause = f"AND {' AND '.join(filters)}" if filters else ""

        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            # Try FTS first
            try:
                cursor = await db.execute(
                    f"""
                    SELECT ma.*, bm25(memory_atoms_fts) AS bm25_score
                    FROM memory_atoms_fts
                    JOIN memory_atoms ma ON ma.id = memory_atoms_fts.atom_id
                    WHERE memory_atoms_fts MATCH ? {where_clause}
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (*params, limit),
                )
                rows = await cursor.fetchall()
            except Exception:
                rows = []

            # Fallback to LIKE when FTS returns nothing
            if not rows:
                like_clauses = " OR ".join(["ma.content LIKE ?" for _ in tokens])
                like_params_full: list[Any] = [f"%{t}%" for t in tokens]
                status_filter = (
                    "AND ma.status = 'active' AND ma.expires_at > ?"
                    if not include_expired
                    else ""
                )
                if not include_expired:
                    like_params_full.append(time.time())
                session_filter = (
                    "AND ma.session_id = ?" if session_id is not None else ""
                )
                persona_filter = (
                    "AND ma.persona_id = ?" if persona_id is not None else ""
                )
                if session_id is not None:
                    like_params_full.append(session_id)
                if persona_id is not None:
                    like_params_full.append(persona_id)
                like_params_full.extend(ownership_params)
                cursor = await db.execute(
                    f"""
                    SELECT ma.*, 0.5 AS bm25_score
                    FROM memory_atoms ma
                    WHERE ({like_clauses}) {status_filter} {session_filter} {persona_filter} AND {ownership}
                    ORDER BY ma.id DESC
                    LIMIT ?
                    """,
                    (*like_params_full, limit),
                )
                rows = await cursor.fetchall()

        if not rows:
            return []

        scores = [float(row["bm25_score"]) for row in rows]
        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score

        atoms: list[MemoryAtom] = []
        now = time.time()
        for row in rows:
            atom = self._row_to_atom(row)
            normalized = (
                1.0
                if score_range == 0
                else (max_score - float(row["bm25_score"])) / score_range
            )
            atom.metadata["bm25_score"] = normalized
            atom.metadata["temporal_score"] = atom.compute_temporal_score(now)
            atoms.append(atom)

        atoms.sort(
            key=lambda a: (
                float(a.metadata.get("bm25_score", 0))
                * float(a.metadata.get("temporal_score", 1))
            ),
            reverse=True,
        )
        return atoms

    async def touch(self, atom_id: int) -> None:
        """Update last_accessed_at for an atom."""
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET last_accessed_at = ? WHERE id = ?",
                (now, atom_id),
            )
            await db.commit()

    async def reinforce(
        self, atom_id: int, new_confidence: float | None = None
    ) -> None:
        """Record a reinforcement event, extending TTL and optionally boosting confidence."""
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # 抢占写锁，避免并发 reinforce 对同一 atom 产生读-改-写丢失更新。
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT reinforcement_count, importance, confidence, atom_type, event_time FROM memory_atoms WHERE id = ?",
                (atom_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return

            new_count = int(row["reinforcement_count"]) + 1
            importance = float(row["importance"])
            atom_type = AtomType(row["atom_type"])
            event_time = float(row["event_time"]) if row["event_time"] else None
            new_ttl, decay = compute_ttl(atom_type, importance, new_count, event_time)

            confidence = (
                new_confidence
                if new_confidence is not None
                else float(row["confidence"])
            )
            # EMA update if new_confidence provided
            if new_confidence is not None:
                confidence = float(row["confidence"]) * 0.7 + new_confidence * 0.3

            await db.execute(
                """
                UPDATE memory_atoms
                SET reinforcement_count = ?, confidence = ?,
                    ttl_days = ?, expires_at = ?, decay_type = ?,
                    last_reinforced_at = ?
                WHERE id = ?
                """,
                (
                    new_count,
                    confidence,
                    new_ttl,
                    now + new_ttl * 86400.0,
                    decay.value,
                    now,
                    atom_id,
                ),
            )
            await db.commit()

    async def expire_stale_atoms(self) -> int:
        """Mark atoms whose expires_at has passed as EXPIRED. Returns count."""
        now = time.time()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE memory_atoms SET status = ? WHERE status = 'active' AND expires_at < ?",
                (AtomStatus.EXPIRED.value, now),
            )
            await db.commit()
            return cursor.rowcount

    async def cleanup_forgotten(self, older_than_days: float = 7.0) -> int:
        """Remove FORGOTTEN atoms older than the threshold from FTS. Returns count."""
        cutoff = time.time() - older_than_days * 86400.0
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id FROM memory_atoms WHERE status = 'forgotten' AND expires_at < ?",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            atom_ids = [int(row[0]) for row in rows]
            if atom_ids:
                placeholders = ",".join("?" * len(atom_ids))
                await db.execute(
                    f"DELETE FROM memory_atoms_fts WHERE atom_id IN ({placeholders})",
                    atom_ids,
                )
                await db.execute(
                    f"DELETE FROM memory_atoms WHERE id IN ({placeholders})",
                    atom_ids,
                )
                await db.commit()
            return len(atom_ids)

    async def forget_expired_atoms(self, older_than_days: float = 7.0) -> int:
        """Soft-delete old expired atoms and remove them from the FTS index."""
        cutoff = time.time() - older_than_days * 86400.0
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id FROM memory_atoms WHERE status = 'expired' AND expires_at < ?",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            atom_ids = [int(row[0]) for row in rows]
            if atom_ids:
                placeholders = ",".join("?" * len(atom_ids))
                await db.execute(
                    f"DELETE FROM memory_atoms_fts WHERE atom_id IN ({placeholders})",
                    atom_ids,
                )
                await db.execute(
                    f"""
                    UPDATE memory_atoms
                    SET status = ?
                    WHERE id IN ({placeholders})
                    """,
                    (AtomStatus.FORGOTTEN.value, *atom_ids),
                )
                await db.commit()
            return len(atom_ids)

    async def delete_by_parent(self, parent_memory_id: int) -> int:
        """Delete all atoms belonging to a parent memory. Returns count."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id FROM memory_atoms WHERE parent_memory_id = ?",
                (parent_memory_id,),
            )
            rows = await cursor.fetchall()
            atom_ids = [int(row[0]) for row in rows]
            if atom_ids:
                placeholders = ",".join("?" * len(atom_ids))
                await db.execute(
                    f"DELETE FROM memory_atoms_fts WHERE atom_id IN ({placeholders})",
                    atom_ids,
                )
                await db.execute(
                    f"DELETE FROM memory_atoms WHERE id IN ({placeholders})",
                    atom_ids,
                )
                await db.commit()
            return len(atom_ids)

    async def batch_delete_by_parent(self, parent_memory_ids: list[int]) -> int:
        """Delete atoms for multiple parent memories in bulk."""
        normalized_ids = sorted({int(item) for item in parent_memory_ids})
        if not normalized_ids:
            return 0

        deleted_count = 0
        async with self._connect() as db:
            for index in range(0, len(normalized_ids), self._SQLITE_BATCH_SIZE):
                batch = normalized_ids[index : index + self._SQLITE_BATCH_SIZE]
                parent_placeholders = ",".join("?" * len(batch))
                cursor = await db.execute(
                    f"""
                    SELECT id
                    FROM memory_atoms
                    WHERE parent_memory_id IN ({parent_placeholders})
                    """,
                    batch,
                )
                rows = await cursor.fetchall()
                atom_ids = [int(row[0]) for row in rows]
                if not atom_ids:
                    continue

                atom_placeholders = ",".join("?" * len(atom_ids))
                await db.execute(
                    f"DELETE FROM memory_atoms_fts WHERE atom_id IN ({atom_placeholders})",
                    atom_ids,
                )
                cursor = await db.execute(
                    f"DELETE FROM memory_atoms WHERE id IN ({atom_placeholders})",
                    atom_ids,
                )
                deleted_count += cursor.rowcount

            if deleted_count:
                await db.commit()
        return deleted_count

    async def get_stats(self) -> dict[str, int]:
        """Return per-status atom counts."""
        ownership, ownership_params = adapter_sql("metadata")
        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT status, COUNT(*) AS cnt FROM memory_atoms WHERE {ownership} GROUP BY status",
                ownership_params,
            )
            rows = await cursor.fetchall()
        stats: dict[str, int] = {s.value: 0 for s in AtomStatus}
        for row in rows:
            stats[row["status"]] = int(row["cnt"])
        return stats

    async def count_atoms(self) -> int:
        """Return the total number of atoms."""
        ownership, ownership_params = adapter_sql("metadata")
        async with self._read_connect() as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM memory_atoms WHERE {ownership}", ownership_params
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def count_by_type(self) -> dict[str, int]:
        """Return per-type atom counts for frontend display."""
        ownership, ownership_params = adapter_sql("metadata")
        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT atom_type, COUNT(*) AS cnt FROM memory_atoms WHERE {ownership} GROUP BY atom_type",
                ownership_params,
            )
            rows = await cursor.fetchall()
        breakdown: dict[str, int] = {}
        for row in rows:
            breakdown[row["atom_type"]] = int(row["cnt"])
        return breakdown

    def _row_to_atom(self, row: aiosqlite.Row) -> MemoryAtom:
        """Map a database row to a MemoryAtom instance."""
        return MemoryAtom(
            atom_id=int(row["id"]),
            parent_memory_id=int(row["parent_memory_id"]),
            atom_type=AtomType(row["atom_type"]),
            content=row["content"],
            entities=json.loads(row["entities"]) if row["entities"] else [],
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            last_accessed_at=float(row["last_accessed_at"]),
            last_reinforced_at=float(row["last_reinforced_at"])
            if row["last_reinforced_at"]
            else None,
            event_time=float(row["event_time"]) if row["event_time"] else None,
            ttl_days=float(row["ttl_days"]),
            expires_at=float(row["expires_at"]),
            status=AtomStatus(row["status"]),
            reinforcement_count=int(row["reinforcement_count"]),
            decay_type=DecayType(row["decay_type"]),
            session_id=row["session_id"],
            persona_id=row["persona_id"],
            metadata=safe_json_dict(row["metadata"]),
        )


__all__ = ["AtomStore"]
