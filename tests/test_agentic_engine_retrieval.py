"""Beta 自主检索引擎入口（search_memories_agentic）的测试。"""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.models.memory_atom import AtomStatus, MemoryAtom
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import BM25Retriever
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import (
    HybridResult,
    HybridRetriever,
)
from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import RRFFusion
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
from tests.test_memory_engine import _FakeFaissDB
from tests.test_retrieval_components import _DummyBM25, _DummyVector


class _SharedDbStorage:
    """模拟生产环境：document_storage 与 engine 共享同一 SQLite。"""

    def __init__(self, holder):
        self._holder = holder

    async def _all_docs(self):
        engine = self._holder["engine"]
        cursor = await engine.db_connection.execute(
            "SELECT id, text, metadata FROM documents ORDER BY id"
        )
        docs = []
        for row in await cursor.fetchall():
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            docs.append({"id": row["id"], "text": row["text"], "metadata": metadata})
        return docs

    async def get_documents(self, metadata_filters, ids=None, limit=50, offset=0):
        docs = await self._all_docs()
        if ids is not None:
            id_set = {int(i) for i in ids}
            docs = [d for d in docs if int(d["id"]) in id_set]
        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]
        return docs[offset : offset + limit]

    async def count_documents(self, metadata_filters):
        docs = await self._all_docs()
        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]
        return len(docs)


class _SharedDbFaiss(_FakeFaissDB):
    """假 FAISS 同时把文档写进 engine 的 SQLite，贴近生产共享库。"""

    def __init__(self, holder):
        super().__init__()
        self._holder = holder
        self.document_storage = _SharedDbStorage(holder)

    async def insert(self, content: str, metadata: dict) -> int:
        doc_id = await super().insert(content, metadata)
        engine = self._holder["engine"]
        await engine.db_connection.execute(
            "INSERT INTO documents (id, doc_id, text, metadata) VALUES (?, ?, ?, ?)",
            (
                doc_id,
                f"uuid-{doc_id}",
                content,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        await engine.db_connection.commit()
        return doc_id


def _identities(*keys: str) -> list[dict]:
    return [
        {
            "identity_key": key,
            "sender_id": key.split(":", 1)[-1],
            "platform": key.split(":", 1)[0],
            "display_name": "测试用户",
            "aliases": [],
            "is_bot": False,
        }
        for key in keys
    ]


async def _make_engine(tmp_path: Path, name: str = "agentic.db") -> MemoryEngine:
    holder: dict = {}
    engine = MemoryEngine(
        db_path=str(tmp_path / name),
        faiss_db=_SharedDbFaiss(holder),
        graph_vector_db=_FakeFaissDB(),
        config={
            "fallback_enabled": True,
            "rrf_k": 60,
            "graph_memory_enabled": True,
        },
    )
    holder["engine"] = engine
    await engine.initialize()
    return engine


class TestConversationTarget:
    @pytest.mark.asyncio
    async def test_exclude_ids_returns_fresh_candidates(self, tmp_path):
        engine = await _make_engine(tmp_path)
        ids = [
            await engine.add_memory(
                content=f"共同经历事件 number {i} 关键词XYZ", session_id="test:s1"
            )
            for i in range(3)
        ]
        first = await engine.search_memories_agentic(
            target="conversation", query="关键词XYZ", k=1, session_id="test:s1"
        )
        assert first.status == "success"
        assert len(first.results) == 1
        assert first.has_more

        second = await engine.search_memories_agentic(
            target="conversation",
            query="关键词XYZ",
            k=1,
            session_id="test:s1",
            exclude_ids=[first.results[0].doc_id],
        )
        assert second.status == "success"
        assert second.results[0].doc_id != first.results[0].doc_id
        assert second.results[0].doc_id in ids
        await engine.close()

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self, tmp_path):
        engine = await _make_engine(tmp_path)
        out = await engine.search_memories_agentic(
            target="conversation", query="   ", k=3, session_id="test:s1"
        )
        assert out.status == "invalid_query"
        await engine.close()

    @pytest.mark.asyncio
    async def test_time_range_filters_by_source_time(self, tmp_path):
        engine = await _make_engine(tmp_path)
        jan_id = await engine.add_memory(
            content="一月爬山的约定",
            session_id="test:s1",
            metadata={"source_time_start": "2026-01-10T10:00:00"},
        )
        feb_id = await engine.add_memory(
            content="二月看雪的约定",
            session_id="test:s1",
            metadata={"source_time_start": "2026-02-10T10:00:00"},
        )
        no_time_id = await engine.add_memory(
            content="没有来源时间的看海约定", session_id="test:s1"
        )
        start = time.mktime(time.strptime("2026-02-01", "%Y-%m-%d"))
        end = time.mktime(time.strptime("2026-02-28", "%Y-%m-%d"))
        out = await engine.search_memories_agentic(
            target="conversation",
            query="约定",
            k=5,
            session_id="test:s1",
            start_ts=start,
            end_ts=end,
        )
        returned = {r.doc_id for r in out.results}
        assert feb_id in returned
        assert jan_id not in returned
        # 来源时间未知的记忆不伪造为符合条件
        assert no_time_id not in returned
        await engine.close()

    @pytest.mark.asyncio
    async def test_no_recent_backfill_in_results(self, tmp_path):
        """主动策略不并入与 query 无关的近期记忆。"""
        engine = await _make_engine(tmp_path)
        await engine.add_memory(content="完全无关的今天午饭", session_id="test:s1")
        await engine.add_memory(content="有关约定的往事", session_id="test:s1")
        out = await engine.search_memories_agentic(
            target="conversation", query="约定", k=3, session_id="test:s1"
        )
        assert out.status == "success"
        for result in out.results:
            assert "recent_memory" not in (result.score_breakdown or {})
        await engine.close()

    @pytest.mark.asyncio
    async def test_scope_isolation(self, tmp_path):
        engine = await _make_engine(tmp_path)
        await engine.add_memory(content="隔离会话的约定", session_id="test:other")
        out = await engine.search_memories_agentic(
            target="conversation", query="约定", k=3, session_id="test:s1"
        )
        assert out.results == []
        assert out.status == "no_candidates"
        await engine.close()


class TestCurrentSpeakerTarget:
    @pytest.mark.asyncio
    async def test_confirmed_and_legacy_candidates(self, tmp_path):
        engine = await _make_engine(tmp_path)
        confirmed_id = await engine.add_memory(
            content="账号A一起打游戏的回忆",
            session_id="test:s1",
            metadata={
                "participant_identities": _identities("qq:1001"),
                "source_time_start": "2026-03-01T09:00:00",
            },
        )
        legacy_id = await engine.add_memory(
            content="小明一起去爬山的老回忆",
            session_id="test:s1",
            metadata={
                "participants": ["小明"],
                "source_time_start": "2026-01-01T09:00:00",
            },
        )
        stranger_id = await engine.add_memory(
            content="小红 unrelated 记忆",
            session_id="test:s1",
            metadata={"participants": ["小红"]},
        )
        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
            identity_candidates=["小明"],
        )
        ids = {r.doc_id for r in out.results}
        assert confirmed_id in ids
        assert legacy_id in ids
        assert stranger_id not in ids
        # 混合命中：结构化与旧昵称候选并存，顶层状态必须是 mixed
        assert out.identity_status == "mixed"
        assert out.match_basis[confirmed_id] == "identity_confirmed"
        assert out.match_basis[legacy_id] == "legacy_candidate"
        await engine.close()

    @pytest.mark.asyncio
    async def test_same_name_different_account_not_merged(self, tmp_path):
        engine = await _make_engine(tmp_path)
        doc_a = await engine.add_memory(
            content="小明A账号的经历",
            session_id="test:s1",
            metadata={"participant_identities": _identities("qq:1001")},
        )
        doc_b = await engine.add_memory(
            content="小明B账号的经历",
            session_id="test:s1",
            metadata={"participant_identities": _identities("qq:2002")},
        )
        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
            identity_candidates=["小明"],
        )
        basis = out.match_basis
        # A 是结构化身份命中；B 仅因昵称候选被返回，绝不能标为已确认
        assert basis.get(doc_a) == "identity_confirmed"
        assert basis.get(doc_b) != "identity_confirmed"
        await engine.close()

    @pytest.mark.asyncio
    async def test_cursor_pagination_reaches_older_memories(
        self, tmp_path, monkeypatch
    ):
        from astrbot_plugin_livingmemory.core.managers import memory_engine_participant

        monkeypatch.setattr(memory_engine_participant, "_PARTICIPANT_PAGE_SIZE", 2)
        engine = await _make_engine(tmp_path)
        ids = []
        for i in range(5):
            day = f"2026-01-{i + 1:02d}T08:00:00"
            ids.append(
                await engine.add_memory(
                    content=f"账号A的第{i}段经历",
                    session_id="test:s1",
                    metadata={
                        "participant_identities": _identities("qq:1001"),
                        "source_time_start": day,
                    },
                )
            )
        page1 = await engine.search_memories_agentic(
            target="current_speaker",
            k=2,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert [r.doc_id for r in page1.results] == [ids[4], ids[3]]
        assert page1.has_more and page1.next_cursor

        page2 = await engine.search_memories_agentic(
            target="current_speaker",
            k=2,
            session_id="test:s1",
            identity_key="qq:1001",
            cursor=page1.next_cursor,
        )
        assert [r.doc_id for r in page2.results] == [ids[2], ids[1]]
        await engine.close()

    @pytest.mark.asyncio
    async def test_pagination_with_default_batch_keeps_unreturned_rows(self, tmp_path):
        """默认批量(200)远大于 k 时，未返回的行必须能按游标续查（R3）。"""
        engine = await _make_engine(tmp_path)
        ids = []
        for i in range(8):
            day = f"2026-02-{i + 1:02d}T08:00:00"
            ids.append(
                await engine.add_memory(
                    content=f"默认批量第{i}段经历",
                    session_id="test:s1",
                    metadata={
                        "participant_identities": _identities("qq:1001"),
                        "source_time_start": day,
                    },
                )
            )
        page1 = await engine.search_memories_agentic(
            target="current_speaker",
            k=2,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert [r.doc_id for r in page1.results] == [ids[7], ids[6]]
        assert page1.has_more and page1.next_cursor

        collected = {r.doc_id for r in page1.results}
        cursor = page1.next_cursor
        for _ in range(4):
            page = await engine.search_memories_agentic(
                target="current_speaker",
                k=2,
                session_id="test:s1",
                identity_key="qq:1001",
                cursor=cursor,
            )
            collected.update(r.doc_id for r in page.results)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert collected == set(ids)
        await engine.close()

    @pytest.mark.asyncio
    async def test_cursor_combined_with_exclusions_does_not_skip_rows(self, tmp_path):
        """游标与排除列表并用不能双重跳行（R3）。"""
        engine = await _make_engine(tmp_path)
        ids = []
        for i in range(6):
            day = f"2026-03-{i + 1:02d}T08:00:00"
            ids.append(
                await engine.add_memory(
                    content=f"组合翻页第{i}段",
                    session_id="test:s1",
                    metadata={
                        "participant_identities": _identities("qq:1001"),
                        "source_time_start": day,
                    },
                )
            )
        page1 = await engine.search_memories_agentic(
            target="current_speaker", k=2, session_id="test:s1", identity_key="qq:1001"
        )
        first_ids = [r.doc_id for r in page1.results]
        page2 = await engine.search_memories_agentic(
            target="current_speaker",
            k=2,
            session_id="test:s1",
            identity_key="qq:1001",
            cursor=page1.next_cursor,
            exclude_ids=first_ids,
        )
        # 排除的是已返回的两条；游标之后应顺延两条，而不是跳行
        assert [r.doc_id for r in page2.results] == [ids[3], ids[2]]
        await engine.close()

    @pytest.mark.asyncio
    async def test_missing_identity_reports_unavailable(self, tmp_path):
        engine = await _make_engine(tmp_path)
        out = await engine.search_memories_agentic(
            target="current_speaker", k=5, session_id="test:s1", identity_key=""
        )
        assert out.status == "identity_unavailable"
        await engine.close()

    @pytest.mark.asyncio
    async def test_identity_scope_isolation(self, tmp_path):
        engine = await _make_engine(tmp_path)
        await engine.add_memory(
            content="另一个会话中账号A的经历",
            session_id="test:other",
            metadata={"participant_identities": _identities("qq:1001")},
        )
        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert out.results == []
        await engine.close()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_forgotten_atoms_hidden_from_agentic_recall(self, tmp_path):
        engine = await _make_engine(tmp_path)
        atoms = [
            MemoryAtom(parent_memory_id=0, content="仍然有效的事实A"),
            MemoryAtom(parent_memory_id=0, content="将被遗忘的事实B"),
        ]
        mid = await engine.add_memory(
            content="summary",
            session_id="test:s1",
            atoms=atoms,
            metadata={"participant_identities": _identities("qq:1001")},
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status = ? WHERE parent_memory_id = ? AND content LIKE ?",
            (AtomStatus.FORGOTTEN.value, mid, "%被遗忘%"),
        )
        await engine.db_connection.commit()

        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert len(out.results) == 1
        content = out.results[0].content
        assert "仍然有效的事实A" in content
        assert "将被遗忘的事实B" not in content

        lifecycle = await engine.get_memory_atom_lifecycle(mid)
        assert lifecycle is not None
        assert lifecycle["hidden"] == 1
        assert lifecycle["active"] == 1
        await engine.close()

    @pytest.mark.asyncio
    async def test_fully_forgotten_parent_omitted(self, tmp_path):
        engine = await _make_engine(tmp_path)
        atoms = [MemoryAtom(parent_memory_id=0, content="唯一的事实将被遗忘")]
        mid = await engine.add_memory(
            content="summary",
            session_id="test:s1",
            atoms=atoms,
            metadata={"participant_identities": _identities("qq:1001")},
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status = ? WHERE parent_memory_id = ?",
            (AtomStatus.FORGOTTEN.value, mid),
        )
        await engine.db_connection.commit()
        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert out.results == []
        assert out.status == "no_candidates"
        await engine.close()


class TestRerankDegradation:
    @pytest.mark.asyncio
    async def test_rerank_exception_preserves_fused_candidates(self):
        """Rerank 抛异常时保留原融合候选，不得解释为"没有相关记忆"。"""

        class _RaisingRerankProvider:
            async def rerank(self, *args, **kwargs):
                raise RuntimeError("rerank exploded")

        retriever = HybridRetriever(
            bm25_retriever=cast(BM25Retriever, _DummyBM25()),
            vector_retriever=cast(VectorRetriever, _DummyVector()),
            rrf_fusion=RRFFusion(k=60),
            config={
                "rerank_enabled": True,
                "rerank_candidates": 5,
                "fallback_enabled": True,
            },
            rerank_provider_resolver=lambda: _RaisingRerankProvider(),
        )
        results = await retriever.search("query", k=2, session_id="s1", persona_id="p1")
        assert len(results) == 2
        assert {r.doc_id for r in results} == {1, 2}

    @pytest.mark.asyncio
    async def test_rerank_resolver_error_preserves_fused_candidates(self):
        def _broken_resolver():
            raise RuntimeError("no provider")

        retriever = HybridRetriever(
            bm25_retriever=cast(BM25Retriever, _DummyBM25()),
            vector_retriever=cast(VectorRetriever, _DummyVector()),
            rrf_fusion=RRFFusion(k=60),
            config={
                "rerank_enabled": True,
                "rerank_candidates": 5,
                "fallback_enabled": True,
            },
            rerank_provider_resolver=_broken_resolver,
        )
        results = await retriever.search("query", k=2)
        assert len(results) == 2


class TestSourceLifecycleConservatism:
    """R2：原文深读的保守可见性判定。"""

    async def _read(self, engine, mid, **kwargs):
        from astrbot_plugin_livingmemory.core.tools.memory_evidence_reader import (
            read_memory_evidence_core,
        )

        return await read_memory_evidence_core(
            memory_engine=engine,
            recall_session_id="test:s1",
            recall_persona_id=None,
            memory_id=mid,
            include_source=True,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_expired_but_unswept_atoms_block_source(self, tmp_path):
        """已到期但尚未被定时清理的原子：事实不含它，原文也必须被扣住。"""
        future = time.time() + 86400 * 30
        atoms = [
            MemoryAtom(
                parent_memory_id=0,
                content="仍然有效的事实",
                expires_at=future,
            ),
            MemoryAtom(
                parent_memory_id=0,
                content="已到期未清理的秘密",
                expires_at=future,
            ),
        ]
        source = [
            {"role": "user", "sender_id": "u1", "timestamp": 1.0, "content": "原文"}
        ]
        engine = await _make_engine(tmp_path)
        mid = await engine.add_memory(
            content="summary", session_id="test:s1", atoms=atoms, source_messages=source
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET expires_at = 1 WHERE parent_memory_id = ? AND content LIKE ?",
            (mid, "%已到期%"),
        )
        await engine.db_connection.commit()

        result = await self._read(engine, mid)
        assert result["status"] == "success"
        assert result["source_status"] == "lifecycle_limited"
        assert result["source_messages"] == []
        assert any("仍然有效" in fact for fact in result["facts"])
        assert all("秘密" not in fact for fact in result["facts"])
        await engine.close()

    @pytest.mark.asyncio
    async def test_purging_forgotten_atoms_does_not_unblock_source(self, tmp_path):
        """遗忘原子被物理清理后，表中"没有 hidden"不能反过来证明原文干净。"""
        future = time.time() + 86400 * 30
        atoms = [
            MemoryAtom(parent_memory_id=0, content="有效事实", expires_at=future),
            MemoryAtom(parent_memory_id=0, content="被遗忘的秘密", expires_at=future),
        ]
        source = [
            {"role": "user", "sender_id": "u1", "timestamp": 1.0, "content": "原文"}
        ]
        engine = await _make_engine(tmp_path)
        mid = await engine.add_memory(
            content="summary", session_id="test:s1", atoms=atoms, source_messages=source
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status = 'forgotten', expires_at = 1 "
            "WHERE parent_memory_id = ? AND content = ?",
            (mid, "被遗忘的秘密"),
        )
        await engine.db_connection.commit()
        assert await engine.atom_store.cleanup_forgotten(older_than_days=0) == 1
        lifecycle = await engine.get_memory_atom_lifecycle(mid)
        assert lifecycle is not None and lifecycle["total"] == 1
        assert lifecycle["hidden"] == 0

        result = await self._read(engine, mid)
        assert result["source_status"] == "lifecycle_limited"
        assert result["source_messages"] == []
        assert any("有效事实" in fact for fact in result["facts"])
        await engine.close()

    @pytest.mark.asyncio
    async def test_beta_keeps_forgotten_hidden_when_atom_feature_is_off(self, tmp_path):
        """库中已有原子数据但配置关闭原子化：Beta 深读仍按生命周期过滤。"""
        future = time.time() + 86400 * 30
        atoms = [
            MemoryAtom(parent_memory_id=0, content="有效事实", expires_at=future),
            MemoryAtom(parent_memory_id=0, content="被遗忘的秘密", expires_at=future),
        ]
        engine_on = await _make_engine(tmp_path, name="shared.db")
        mid = await engine_on.add_memory(
            content="summary", session_id="test:s1", atoms=atoms
        )
        await engine_on.db_connection.execute(
            "UPDATE memory_atoms SET status = ? WHERE parent_memory_id = ? AND content LIKE ?",
            (AtomStatus.FORGOTTEN.value, mid, "%秘密%"),
        )
        await engine_on.db_connection.commit()
        await engine_on.close()

        holder: dict = {}
        engine_off = MemoryEngine(
            db_path=str(tmp_path / "shared.db"),
            faiss_db=_SharedDbFaiss(holder),
            graph_vector_db=_FakeFaissDB(),
            config={
                "fallback_enabled": True,
                "rrf_k": 60,
                "graph_memory_enabled": True,
                "atom_enabled": False,
            },
        )
        holder["engine"] = engine_off
        await engine_off.initialize()
        assert engine_off.atom_enabled is False

        result = await self._read(engine_off, mid)
        assert result["status"] == "success"
        assert any("有效事实" in fact for fact in result["facts"])
        assert all("秘密" not in fact for fact in result["facts"])
        await engine_off.close()


class TestBoundedTimeFilter:
    """R8：时间过滤不能把"候选池截断"误报成"没有更多"。"""

    @pytest.mark.asyncio
    async def test_time_filter_does_not_false_report_empty_due_to_small_pool(
        self, tmp_path
    ):
        engine = await _make_engine(tmp_path)
        for i in range(3):
            await engine.add_memory(
                content=f"一月约定事件{i}",
                session_id="test:s1",
                metadata={"source_time_start": f"2026-01-{i + 5:02d}T10:00:00"},
            )
        march_id = await engine.add_memory(
            content="三月约定事件",
            session_id="test:s1",
            metadata={"source_time_start": "2026-03-10T10:00:00"},
        )
        start = time.mktime(time.strptime("2026-03-01", "%Y-%m-%d"))
        end = time.mktime(time.strptime("2026-03-31", "%Y-%m-%d"))
        out = await engine.search_memories_agentic(
            target="conversation",
            query="约定",
            k=1,
            session_id="test:s1",
            start_ts=start,
            end_ts=end,
        )
        assert out.status == "success"
        assert [r.doc_id for r in out.results] == [march_id]
        await engine.close()


class TestLegacyPersonCompat:
    """R9：旧记录兼容（纯文本昵称、unicode 转义 JSON）。"""

    @pytest.mark.asyncio
    async def test_legacy_text_only_person_records_can_be_found(self, tmp_path):
        engine = await _make_engine(tmp_path)
        text_only_id = await engine.add_memory(
            content="小明和大家一起去黄山看日出的老经历",
            session_id="test:s1",
            metadata={"source_time_start": "2025-11-02T08:00:00"},
        )
        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
            identity_candidates=["小明"],
        )
        ids = {r.doc_id for r in out.results}
        assert text_only_id in ids
        assert out.match_basis[text_only_id] == "text_legacy"
        assert out.identity_status == "text_legacy"
        await engine.close()

    @pytest.mark.asyncio
    async def test_legacy_json_unicode_escaping_does_not_hide_person(self, tmp_path):
        engine = await _make_engine(tmp_path)
        metadata = {
            "session_id": "test:s1",
            "status": "active",
            "participants": ["小明"],
            "source_time_start": "2025-12-01T08:00:00",
        }
        cursor = await engine.db_connection.execute(
            "INSERT INTO documents (doc_id, text, metadata) VALUES (?, ?, ?)",
            (
                "legacy-1",
                "unicode 转义的旧记录",
                json.dumps(metadata, ensure_ascii=True),
            ),
        )
        await engine.db_connection.commit()
        legacy_id = cursor.lastrowid

        out = await engine.search_memories_agentic(
            target="current_speaker",
            k=5,
            session_id="test:s1",
            identity_key="qq:1001",
            identity_candidates=["小明"],
        )
        ids = {r.doc_id for r in out.results}
        assert legacy_id in ids
        assert out.match_basis[legacy_id] == "legacy_candidate"
        await engine.close()


class TestPersonTopicRefinement:
    """R10：人物起步后可用主题关键词真实收窄。"""

    @pytest.mark.asyncio
    async def test_person_topic_refinement_is_not_ignored(self, tmp_path):
        engine = await _make_engine(tmp_path)
        hike_id = await engine.add_memory(
            content="账号A较早年一起爬山的 Equipment 经历",
            session_id="test:s1",
            metadata={
                "participant_identities": _identities("qq:1001"),
                "source_time_start": "2026-01-10T08:00:00",
            },
        )
        lunch_id = await engine.add_memory(
            content="账号A最近一起午餐的 Equipment 闲聊",
            session_id="test:s1",
            metadata={
                "participant_identities": _identities("qq:1001"),
                "source_time_start": "2026-08-10T08:00:00",
            },
        )
        out = await engine.search_memories_agentic(
            target="current_speaker",
            query="爬山",
            k=1,
            session_id="test:s1",
            identity_key="qq:1001",
        )
        assert out.status == "success"
        assert [r.doc_id for r in out.results] == [hike_id]
        assert lunch_id not in {r.doc_id for r in out.results}
        await engine.close()


class TestBetaRelevanceOrdering:
    """R13：查旧事时相关性命中不被新鲜度权重翻转。"""

    @pytest.mark.asyncio
    async def test_old_relevant_memory_not_displaced_by_recency_in_beta(self, tmp_path):
        engine = await _make_engine(tmp_path)
        now = time.time()
        old_result = HybridResult(
            doc_id=1,
            final_score=0.5,
            rrf_score=1.0,
            bm25_score=1.0,
            vector_score=1.0,
            content="旧的相关记忆",
            metadata={
                "session_id": "test:s1",
                "status": "active",
                "importance": 0.5,
                "create_time": now - 86400 * 400,
            },
        )
        new_result = HybridResult(
            doc_id=2,
            final_score=0.9,
            rrf_score=0.5,
            bm25_score=0.5,
            vector_score=0.5,
            content="新的弱相关记忆",
            metadata={
                "session_id": "test:s1",
                "status": "active",
                "importance": 0.5,
                "create_time": now,
            },
        )

        rows = [
            SimpleNamespace(
                doc_id=r.doc_id,
                content=r.content,
                metadata=r.metadata,
                score=1.0 - i / 10,
            )
            for i, r in enumerate((old_result, new_result))
        ]
        # 执行真实融合和加权，不用预先排好序的顶层检索替身。
        engine.dual_route_retriever = None
        engine.atom_retriever = None
        engine.hybrid_retriever.bm25_retriever.search = AsyncMock(return_value=rows)
        engine.hybrid_retriever.vector_retriever.search = AsyncMock(return_value=rows)
        legacy = await engine.search_memories(query="记忆", k=2, session_id="test:s1")
        assert [r.doc_id for r in legacy] == [2, 1]

        # Beta：按纯相关性排序，旧的强相关记忆回到首位
        beta = await engine.search_memories_agentic(
            target="conversation", query="记忆", k=2, session_id="test:s1"
        )
        assert beta.status == "success"
        assert [r.doc_id for r in beta.results] == [1, 2]
        await engine.close()


class TestRouteFailureDiagnosis:
    """R6：真实检索失败不能被包装成"没有相关记忆"。"""

    @pytest.mark.asyncio
    async def test_all_retrieval_routes_failing_is_not_reported_as_no_candidates(
        self, tmp_path
    ):
        engine = await _make_engine(tmp_path)

        engine.dual_route_retriever = None
        engine.atom_retriever = None
        engine.hybrid_retriever.bm25_retriever.search = AsyncMock(
            side_effect=RuntimeError("index locked")
        )
        engine.hybrid_retriever.vector_retriever.search = AsyncMock(
            side_effect=RuntimeError("provider down")
        )
        out = await engine.search_memories_agentic(
            target="conversation", query="anything", k=3, session_id="test:s1"
        )
        assert out.status == "error"
        assert "retrieval_routes_failed" in out.message
        assert "bm25" in out.message
        await engine.close()

    @pytest.mark.asyncio
    async def test_retriever_exception_is_error_not_empty_success(self, tmp_path):
        engine = await _make_engine(tmp_path)

        class _ExplodingHybrid:
            async def search(self, *args, **kwargs):
                raise RuntimeError("faiss exploded")

        engine.dual_route_retriever = None
        engine.hybrid_retriever = _ExplodingHybrid()
        out = await engine.search_memories_agentic(
            target="conversation", query="anything", k=3, session_id="test:s1"
        )
        assert out.status == "error"
        assert out.results == []
        await engine.close()
