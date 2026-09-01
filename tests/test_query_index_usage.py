"""
Tests for expression-index usage on hot-path document queries.

These are regression guards: the WHERE/ORDER BY expressions in the recall
path and the WebUI memories list must match the expression indexes created
by MemoryEngine._create_tables exactly (COALESCE/CAST wrappers included),
otherwise SQLite silently degrades to full table scans and temp B-tree
sorts as the memory bank grows.
"""

import json
import time
from pathlib import Path

import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

from .test_memory_engine import _FakeFaissDB


@pytest.mark.asyncio
async def _make_engine(tmp_path: Path) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config={"atom_enabled": False, "graph_memory_enabled": False},
    )
    await engine.initialize()
    return engine


async def _plan(engine: MemoryEngine, sql: str, params: list) -> str:
    cursor = await engine.db_connection.execute("EXPLAIN QUERY PLAN " + sql, params)
    rows = await cursor.fetchall()
    return "\n".join(row["detail"] for row in rows)


RECENT_SQL = (
    # Mirrors _get_recent_memory_results (INDEXED BY forces the
    # create_time range scan; without it the planner picks idx_doc_status
    # plus a temp B-tree sort)
    "SELECT id, text, metadata FROM documents INDEXED BY idx_doc_create_time WHERE "
    "COALESCE(json_extract(metadata, '$.status'), 'active') = 'active' "
    "AND json_extract(metadata, '$.session_id') = ? "
    "AND COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) >= ? "
    "ORDER BY COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) DESC, id DESC "
    "LIMIT ?"
)


@pytest.mark.asyncio
async def test_recent_memory_query_uses_indexes(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        plan = await _plan(
            engine, RECENT_SQL, ["s1", time.time() - 72 * 3600, 15]
        )
        assert "SCAN documents" not in plan, plan
        assert "TEMP B-TREE" not in plan, plan
        assert "idx_doc_create_time" in plan, plan
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_memories_list_filters_use_indexes(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        plan = await _plan(
            engine,
            "SELECT COUNT(*) FROM documents WHERE "
            "json_extract(metadata, '$.session_id') = ? AND "
            "COALESCE(json_extract(metadata, '$.status'), 'active') = ? AND "
            "UPPER(COALESCE(json_extract(metadata, '$.memory_type'), 'GENERAL')) = ?",
            ["s1", "active", "GENERAL"],
        )
        assert "SCAN documents" not in plan, plan

        # 无过滤的默认排序（列表页首屏）必须直接走 create_time 索引，不排序
        plan = await _plan(
            engine,
            "SELECT id FROM documents "
            "ORDER BY COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) DESC, id DESC "
            "LIMIT 20 OFFSET 0",
            [],
        )
        assert "TEMP B-TREE" not in plan, plan
        assert "idx_doc_create_time" in plan, plan
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_created_asc_sort_reuses_create_time_index(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        plan = await _plan(
            engine,
            "SELECT id FROM documents ORDER BY "
            "COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) ASC, id ASC "
            "LIMIT 20",
            [],
        )
        assert "TEMP B-TREE" not in plan, plan
        assert "idx_doc_create_time" in plan, plan
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_new_indexes_exist_after_initialize(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        cursor = await engine.db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_doc_%'"
        )
        names = {row["name"] for row in await cursor.fetchall()}
        assert {
            "idx_doc_metadata",
            "idx_doc_persona_metadata",
            "idx_doc_status",
            "idx_doc_memory_type",
            "idx_doc_create_time",
        } <= names
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recent_memory_results_still_correct_with_coalesce_expr(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        now = time.time()
        for i in range(5):
            await engine.db_connection.execute(
                "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
                (
                    i + 1,
                    f"uuid-{i + 1}",
                    f"m{i}",
                    json.dumps(
                        {
                            "session_id": "s1",
                            "status": "active",
                            "create_time": now - (4 - i) * 3600,
                        }
                    ),
                ),
            )
        # memory missing create_time must be excluded by the age window
        await engine.db_connection.execute(
            "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (99, "uuid-99", "no-time", json.dumps({"session_id": "s1"})),
        )
        await engine.db_connection.commit()

        results = await engine._get_recent_memory_results(3, "s1", None)
        assert [r.doc_id for r in results] == [5, 4, 3]
        assert all(r.doc_id != 99 for r in results)
    finally:
        await engine.close()
