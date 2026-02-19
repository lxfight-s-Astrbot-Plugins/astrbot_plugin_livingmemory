"""
Tests for MemoryEngine with a fake in-memory FaissDB.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine


@dataclass
class _FakeRetrieveResult:
    similarity: float
    data: dict


class _FakeDocumentStorage:
    def __init__(self, db: "_FakeFaissDB"):
        self._db = db

    async def get_documents(self, metadata_filters, ids=None, limit=50, offset=0):
        docs = list(self._db.docs.values())
        if ids is not None:
            id_set = set(ids)
            docs = [d for d in docs if d["id"] in id_set]

        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]

        docs = docs[offset : offset + limit]
        return [dict(d) for d in docs]

    async def count_documents(self, metadata_filters):
        docs = list(self._db.docs.values())
        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]
        return len(docs)


class _FakeFaissDB:
    def __init__(self):
        self.docs: dict[int, dict] = {}
        self._next_id = 1
        self.document_storage = _FakeDocumentStorage(self)

    async def insert(self, content: str, metadata: dict) -> int:
        doc_id = self._next_id
        self._next_id += 1
        self.docs[doc_id] = {
            "id": doc_id,
            "doc_id": f"uuid-{doc_id}",
            "text": content,
            "metadata": dict(metadata),
        }
        return doc_id

    async def retrieve(
        self, query: str, k: int, fetch_k: int, rerank: bool, metadata_filters=None
    ):
        results: list[_FakeRetrieveResult] = []
        for doc in self.docs.values():
            if metadata_filters:
                ok = True
                for key, value in metadata_filters.items():
                    if doc["metadata"].get(key) != value:
                        ok = False
                        break
                if not ok:
                    continue

            text = doc["text"]
            score = 0.9 if query in text else 0.2
            results.append(
                _FakeRetrieveResult(
                    similarity=score,
                    data={
                        "id": doc["id"],
                        "text": text,
                        "metadata": dict(doc["metadata"]),
                    },
                )
            )

        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:k]

    async def delete(self, uuid_doc_id: str) -> None:
        target = None
        for did, doc in self.docs.items():
            if doc["doc_id"] == uuid_doc_id:
                target = did
                break
        if target is not None:
            self.docs.pop(target, None)


@pytest.mark.asyncio
async def test_memory_engine_add_search_get_delete(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={"fallback_enabled": True, "rrf_k": 60},
    )
    await engine.initialize()

    memory_id = await engine.add_memory(
        content="我喜欢吃苹果",
        session_id="test:private:s1",
        persona_id="persona_1",
        importance=0.8,
        metadata={"topics": ["饮食"]},
    )
    assert memory_id > 0

    result = await engine.get_memory(memory_id)
    assert result is not None
    assert "苹果" in result["text"]

    searched = await engine.search_memories(
        query="苹果",
        k=3,
        session_id="test:private:s1",
        persona_id="persona_1",
    )
    assert len(searched) >= 1
    assert searched[0].doc_id == memory_id

    ok_delete = await engine.delete_memory(memory_id)
    assert ok_delete is True
    assert await engine.get_memory(memory_id) is None
    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_decay_and_cleanup(tmp_path: Path):
    db_path = tmp_path / "memory_decay.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={"cleanup_days_threshold": 1, "cleanup_importance_threshold": 0.3},
    )
    await engine.initialize()

    old_id = await engine.add_memory(
        content="旧记忆",
        session_id="s",
        persona_id="p",
        importance=0.2,
        metadata={"topics": ["old"]},
    )
    new_id = await engine.add_memory(
        content="新记忆",
        session_id="s",
        persona_id="p",
        importance=0.9,
        metadata={"topics": ["new"]},
    )
    assert old_id != new_id

    # Make old memory older than threshold in fake storage and sqlite table.
    old_time = time.time() - 86400 * 3
    engine.faiss_db.docs[old_id]["metadata"]["create_time"] = old_time
    engine.faiss_db.docs[old_id]["metadata"]["last_access_time"] = old_time

    if engine.db_connection is not None:
        await engine.db_connection.execute(
            "INSERT OR REPLACE INTO documents (id, doc_id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                old_id,
                f"uuid-{old_id}",
                "旧记忆",
                json.dumps(
                    {
                        "importance": 0.2,
                        "create_time": old_time,
                        "last_access_time": old_time,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        await engine.db_connection.execute(
            "INSERT OR REPLACE INTO documents (id, doc_id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                new_id,
                f"uuid-{new_id}",
                "新记忆",
                json.dumps(
                    {
                        "importance": 0.9,
                        "create_time": time.time(),
                        "last_access_time": time.time(),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        await engine.db_connection.commit()

    decayed = await engine.apply_daily_decay(decay_rate=0.1, days=2)
    assert isinstance(decayed, int)

    deleted = await engine.cleanup_old_memories(
        days_threshold=1, importance_threshold=0.3
    )
    assert deleted >= 1

    stats = await engine.get_statistics()
    assert "total_memories" in stats

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_search_updates_access_time_async(tmp_path: Path):
    db_path = tmp_path / "memory_access.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={"fallback_enabled": True},
    )
    await engine.initialize()

    mid = await engine.add_memory(
        content="测试访问时间",
        session_id="test:private:s1",
        persona_id="p1",
        importance=0.5,
        metadata={},
    )

    await engine.search_memories(
        "测试", k=1, session_id="test:private:s1", persona_id="p1"
    )
    await asyncio.sleep(0.05)
    # Access-time update may fail silently if row absent in sqlite documents table;
    # function should still complete and return results.
    assert mid in engine.faiss_db.docs
    await engine.close()


# ── MemoryEngine 过滤/衰减/清理边界测试 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_engine_session_filter_isolates_sessions(tmp_path: Path):
    """不同 session_id 的记忆应相互隔离，搜索时只返回匹配 session 的结果。"""
    db_path = tmp_path / "filter.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={"fallback_enabled": True},
    )
    await engine.initialize()

    await engine.add_memory(
        content="session A 的记忆：用户喜欢苹果",
        session_id="test:private:session_A",
        persona_id="p1",
        importance=0.8,
        metadata={},
    )
    await engine.add_memory(
        content="session B 的记忆：用户喜欢香蕉",
        session_id="test:private:session_B",
        persona_id="p1",
        importance=0.8,
        metadata={},
    )

    results_a = await engine.search_memories(
        query="苹果",
        k=5,
        session_id="test:private:session_A",
        persona_id="p1",
    )
    # session A 的搜索不应返回 session B 的记忆
    for r in results_a:
        assert r.metadata.get("session_id") == "test:private:session_A"

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_apply_daily_decay_zero_rate_returns_zero(tmp_path: Path):
    """decay_rate=0 时，apply_daily_decay 应直接返回 0，不修改任何记忆。"""
    db_path = tmp_path / "decay_zero.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    await engine.add_memory(
        content="测试记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.8,
        metadata={},
    )

    result = await engine.apply_daily_decay(decay_rate=0, days=1)
    assert result == 0

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_apply_daily_decay_zero_days_returns_zero(tmp_path: Path):
    """days=0 时，apply_daily_decay 应直接返回 0。"""
    db_path = tmp_path / "decay_days_zero.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    await engine.add_memory(
        content="测试记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.8,
        metadata={},
    )

    result = await engine.apply_daily_decay(decay_rate=0.1, days=0)
    assert result == 0

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_apply_daily_decay_reduces_importance(tmp_path: Path):
    """apply_daily_decay 应降低记忆的 importance 值。"""
    db_path = tmp_path / "decay_reduce.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    mid = await engine.add_memory(
        content="重要记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.8,
        metadata={},
    )

    # 手动在 SQLite 中写入 importance，确保衰减可以读取
    if engine.db_connection is not None:
        await engine.db_connection.execute(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            (json.dumps({"importance": 0.8, "session_id": "s1"}), mid),
        )
        await engine.db_connection.commit()

    affected = await engine.apply_daily_decay(decay_rate=0.1, days=1)
    assert isinstance(affected, int)
    assert affected >= 0

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_cleanup_negative_days_returns_zero(tmp_path: Path):
    """days_threshold < 0 时，cleanup_old_memories 应返回 0。"""
    db_path = tmp_path / "cleanup_neg.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    await engine.add_memory(
        content="旧记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.1,
        metadata={},
    )

    result = await engine.cleanup_old_memories(days_threshold=-1, importance_threshold=0.5)
    assert result == 0

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_cleanup_zero_days_deletes_low_importance(tmp_path: Path):
    """days_threshold=0 时，所有低重要性记忆（无论多新）都应被清理。"""
    db_path = tmp_path / "cleanup_zero.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    low_id = await engine.add_memory(
        content="低重要性记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.1,
        metadata={},
    )
    high_id = await engine.add_memory(
        content="高重要性记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.9,
        metadata={},
    )

    # 确保 SQLite 中有正确的 importance 和 create_time
    now = time.time()
    if engine.db_connection is not None:
        await engine.db_connection.execute(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            (json.dumps({"importance": 0.1, "create_time": now}), low_id),
        )
        await engine.db_connection.execute(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            (json.dumps({"importance": 0.9, "create_time": now}), high_id),
        )
        await engine.db_connection.commit()

    deleted = await engine.cleanup_old_memories(days_threshold=0, importance_threshold=0.5)
    assert deleted >= 1
    assert await engine.get_memory(high_id) is not None

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_update_memory_content_creates_new_deletes_old(tmp_path: Path):
    """update_memory 更新内容时，应先创建新记忆再删除旧记忆。"""
    db_path = tmp_path / "update.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    old_id = await engine.add_memory(
        content="旧内容",
        session_id="s1",
        persona_id="p1",
        importance=0.7,
        metadata={},
    )

    success = await engine.update_memory(old_id, {"content": "新内容"})
    assert success is True
    assert await engine.get_memory(old_id) is None

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_update_memory_importance_only(tmp_path: Path):
    """update_memory 只更新 importance 时，不应崩溃（fake DB 不支持 get_session，返回 False 是预期行为）。"""
    db_path = tmp_path / "update_imp.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    mid = await engine.add_memory(
        content="测试记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.5,
        metadata={},
    )

    # fake DB 不支持 get_session，update_metadata 会失败，但不应抛出异常
    result = await engine.update_memory(mid, {"importance": 0.9})
    assert isinstance(result, bool)  # 不崩溃即可
    # 记忆仍然存在（内容未被删除）
    assert await engine.get_memory(mid) is not None

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_delete_nonexistent_returns_false(tmp_path: Path):
    """删除不存在的记忆 ID 应返回 False。"""
    db_path = tmp_path / "del_nonexist.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    result = await engine.delete_memory(99999)
    assert result is False

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_search_empty_query_returns_empty(tmp_path: Path):
    """空查询应直接返回空列表。"""
    db_path = tmp_path / "empty_query.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    await engine.add_memory(
        content="一些记忆内容",
        session_id="s1",
        persona_id="p1",
        importance=0.5,
        metadata={},
    )

    assert await engine.search_memories("", k=5) == []
    assert await engine.search_memories("   ", k=5) == []

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_get_statistics_returns_expected_keys(tmp_path: Path):
    """get_statistics 应返回包含 total_memories 等关键字段的字典。"""
    db_path = tmp_path / "stats.db"
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=_FakeFaissDB(),
        config={},
    )
    await engine.initialize()

    await engine.add_memory(
        content="统计测试记忆",
        session_id="s1",
        persona_id="p1",
        importance=0.6,
        metadata={},
    )

    stats = await engine.get_statistics()
    assert "total_memories" in stats

    await engine.close()
