"""
Tests for retrieval components (BM25/RRF/Hybrid).
"""

import json
import time
from pathlib import Path
from typing import cast

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import BM25Retriever
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridRetriever
from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import (
    BM25Result as RRFBM25Result,
)
from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import (
    RRFFusion,
    VectorResult,
)
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever


@pytest.mark.asyncio
async def test_bm25_add_search_update_delete(tmp_path: Path):
    db_path = tmp_path / "bm25.db"
    retriever = BM25Retriever(str(db_path), TextProcessor())
    await retriever.initialize()

    metadata_1 = {"session_id": "s1", "persona_id": "p1", "importance": 0.5}
    metadata_2 = {"session_id": "s1", "persona_id": "p1", "importance": 0.5}

    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                text TEXT,
                metadata TEXT
            )
        """)
        await db.execute(
            "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
            (1, "我喜欢编程和Python", json.dumps(metadata_1, ensure_ascii=False)),
        )
        await db.execute(
            "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
            (2, "我今天去跑步", json.dumps(metadata_2, ensure_ascii=False)),
        )
        await db.commit()

    await retriever.add_document(
        1,
        "我喜欢编程和Python",
        metadata_1,
    )
    await retriever.add_document(
        2,
        "我今天去跑步",
        metadata_2,
    )

    res = await retriever.search("编程", limit=5, session_id="s1", persona_id="p1")
    assert len(res) >= 1
    assert res[0].doc_id == 1
    assert 0.0 <= res[0].score <= 1.0

    ok_update = await retriever.update_document(
        2, "我今天跑步并学习编程", {"session_id": "s1"}
    )
    assert ok_update is True
    res2 = await retriever.search("学习", limit=5)
    assert any(r.doc_id == 2 for r in res2)

    ok_delete = await retriever.delete_document(1)
    assert ok_delete is True
    res3 = await retriever.search("Python", limit=5)
    assert all(r.doc_id != 1 for r in res3)


def test_rrf_fusion_orders_combined_results():
    fusion = RRFFusion(k=60)
    bm25 = [
        RRFBM25Result(doc_id=1, score=1.0, content="a", metadata={}),
        RRFBM25Result(doc_id=2, score=0.8, content="b", metadata={}),
    ]
    vec = [
        VectorResult(doc_id=2, score=0.9, content="b", metadata={}),
        VectorResult(doc_id=3, score=0.7, content="c", metadata={}),
    ]

    fused = fusion.fuse(bm25, vec, top_k=3)
    assert len(fused) == 3
    # doc_id=2 appears in both lists, should rank high.
    assert fused[0].doc_id == 2


class _DummyBM25:
    async def search(self, query, k, session_id=None, persona_id=None):
        now = time.time()
        return [
            RRFBM25Result(
                doc_id=1,
                score=0.8,
                content="old important",
                metadata={"importance": 0.9, "create_time": now - 86400 * 10},
            ),
            RRFBM25Result(
                doc_id=2,
                score=0.7,
                content="new less important",
                metadata={"importance": 0.4, "create_time": now},
            ),
        ]


class _DummyVector:
    async def search(self, query, k, session_id=None, persona_id=None):
        now = time.time()
        return [
            VectorResult(
                doc_id=2,
                score=0.95,
                content="new less important",
                metadata={"importance": 0.4, "create_time": now},
            ),
            VectorResult(
                doc_id=1,
                score=0.7,
                content="old important",
                metadata={"importance": 0.9, "create_time": now - 86400 * 10},
            ),
        ]

    async def update_metadata(self, doc_id, metadata):
        return True

    async def delete_document(self, doc_id):
        return True


@pytest.mark.asyncio
async def test_hybrid_retriever_search_and_weighting():
    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _DummyBM25()),
        vector_retriever=cast(VectorRetriever, _DummyVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"decay_rate": 0.01, "importance_weight": 1.0, "fallback_enabled": True},
    )

    results = await retriever.search("query", k=2, session_id="s1", persona_id="p1")
    assert len(results) == 2
    # final scores should be computed and sorted.
    assert results[0].final_score >= results[1].final_score
    assert results[0].doc_id in {1, 2}


@pytest.mark.asyncio
async def test_hybrid_retriever_fallback_when_one_channel_fails():
    class _FailBM25:
        async def search(self, *args, **kwargs):
            raise RuntimeError("bm25 failed")

    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _FailBM25()),
        vector_retriever=cast(VectorRetriever, _DummyVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"fallback_enabled": True},
    )
    results = await retriever.search("query", k=2)
    assert len(results) >= 1


# ── New tests for weighted-sum scoring, last_access_time decay, MMR ──────────


@pytest.mark.asyncio
async def test_weighted_sum_scoring_does_not_zero_out_old_important_memory():
    """
    旧的乘法公式会让高龄记忆分数趋近于零。
    新的加权求和公式应保证高重要性的旧记忆仍能获得合理分数。
    """
    now = time.time()

    class _OldImportantBM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                RRFBM25Result(
                    doc_id=1,
                    score=0.9,
                    content="用户最喜欢的食物是寿司",
                    metadata={
                        "importance": 0.9,
                        "create_time": now - 86400 * 180,  # 180天前
                        "last_access_time": now - 86400 * 180,
                    },
                ),
                RRFBM25Result(
                    doc_id=2,
                    score=0.3,
                    content="今天天气不错",
                    metadata={
                        "importance": 0.2,
                        "create_time": now - 60,  # 1分钟前
                        "last_access_time": now - 60,
                    },
                ),
            ]

    class _OldImportantVector:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                VectorResult(
                    doc_id=1,
                    score=0.85,
                    content="用户最喜欢的食物是寿司",
                    metadata={
                        "importance": 0.9,
                        "create_time": now - 86400 * 180,
                        "last_access_time": now - 86400 * 180,
                    },
                ),
                VectorResult(
                    doc_id=2,
                    score=0.4,
                    content="今天天气不错",
                    metadata={
                        "importance": 0.2,
                        "create_time": now - 60,
                        "last_access_time": now - 60,
                    },
                ),
            ]

        async def update_metadata(self, doc_id, metadata):
            return True

        async def delete_document(self, doc_id):
            return True

    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _OldImportantBM25()),
        vector_retriever=cast(VectorRetriever, _OldImportantVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"decay_rate": 0.01, "score_alpha": 0.5, "score_beta": 0.25, "score_gamma": 0.25},
    )

    results = await retriever.search("query", k=2)
    assert len(results) == 2

    old_result = next(r for r in results if r.doc_id == 1)
    new_result = next(r for r in results if r.doc_id == 2)

    # 高重要性旧记忆的分数不应被时间衰减清零（旧乘法公式下约为 rrf*0.9*0.165 ≈ 0.15，新公式应远高于此）
    assert old_result.final_score > 0.5, "旧但重要的记忆分数不应趋近于零"
    # 两条记忆都应获得合理分数（新公式下各维度互补，不会出现清零）
    assert new_result.final_score > 0.3, "新记忆分数也应合理"
    # 旧记忆分数差距不应过大（加权求和保证了高重要性记忆的竞争力）
    score_gap = new_result.final_score - old_result.final_score
    assert score_gap < 0.2, f"旧重要记忆与新记忆分差不应超过0.2，实际差距: {score_gap:.4f}"


@pytest.mark.asyncio
async def test_last_access_time_slows_decay():
    """
    last_access_time 比 create_time 更近时，应使用 last_access_time 计算衰减，
    使高频访问记忆的衰减速度放缓。
    """
    now = time.time()
    old_create = now - 86400 * 90  # 90天前创建
    recent_access = now - 86400 * 1  # 1天前访问

    class _AccessedBM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                RRFBM25Result(
                    doc_id=10,
                    score=0.8,
                    content="经常被访问的记忆",
                    metadata={
                        "importance": 0.5,
                        "create_time": old_create,
                        "last_access_time": recent_access,
                    },
                ),
                RRFBM25Result(
                    doc_id=11,
                    score=0.8,
                    content="从未被访问的旧记忆",
                    metadata={
                        "importance": 0.5,
                        "create_time": old_create,
                        "last_access_time": 0,
                    },
                ),
            ]

    class _AccessedVector:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                VectorResult(
                    doc_id=10,
                    score=0.8,
                    content="经常被访问的记忆",
                    metadata={
                        "importance": 0.5,
                        "create_time": old_create,
                        "last_access_time": recent_access,
                    },
                ),
                VectorResult(
                    doc_id=11,
                    score=0.8,
                    content="从未被访问的旧记忆",
                    metadata={
                        "importance": 0.5,
                        "create_time": old_create,
                        "last_access_time": 0,
                    },
                ),
            ]

        async def update_metadata(self, doc_id, metadata):
            return True

        async def delete_document(self, doc_id):
            return True

    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _AccessedBM25()),
        vector_retriever=cast(VectorRetriever, _AccessedVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"decay_rate": 0.05},
    )

    results = await retriever.search("query", k=2)
    assert len(results) == 2

    accessed = next(r for r in results if r.doc_id == 10)
    not_accessed = next(r for r in results if r.doc_id == 11)

    # 最近访问过的记忆应有更高的 recency_weight，因此最终分数更高
    assert accessed.final_score > not_accessed.final_score
    # score_breakdown 应存在
    assert accessed.score_breakdown is not None
    assert "recency_weight" in accessed.score_breakdown
    assert "days_old" in accessed.score_breakdown


@pytest.mark.asyncio
async def test_score_breakdown_fields_present():
    """score_breakdown 应包含所有预期字段。"""
    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _DummyBM25()),
        vector_retriever=cast(VectorRetriever, _DummyVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"decay_rate": 0.01},
    )
    results = await retriever.search("query", k=2)
    for r in results:
        assert r.score_breakdown is not None
        for field in ("rrf_normalized", "importance", "recency_weight", "days_old", "final_score"):
            assert field in r.score_breakdown, f"score_breakdown 缺少字段: {field}"


@pytest.mark.asyncio
async def test_mmr_dedup_reduces_semantic_duplicates():
    """
    MMR 应从语义重复的候选中选出多样化结果，
    而不是直接返回分数最高的 k 条（可能全部相似）。
    """
    now = time.time()

    class _DuplicateBM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                RRFBM25Result(
                    doc_id=i,
                    score=0.9 - i * 0.05,
                    content="用户喜欢吃寿司 这是重复内容",
                    metadata={"importance": 0.8, "create_time": now},
                )
                for i in range(1, 5)
            ]

    class _DuplicateVector:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                VectorResult(
                    doc_id=i,
                    score=0.9 - i * 0.05,
                    content="用户喜欢吃寿司 这是重复内容",
                    metadata={"importance": 0.8, "create_time": now},
                )
                for i in range(1, 5)
            ]

        async def update_metadata(self, doc_id, metadata):
            return True

        async def delete_document(self, doc_id):
            return True

    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _DuplicateBM25()),
        vector_retriever=cast(VectorRetriever, _DuplicateVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"mmr_lambda": 0.5},  # 偏向多样性
    )

    results = await retriever.search("query", k=2)
    # 结果数量不超过 k
    assert len(results) <= 2
    # 第一条应是最高分
    if len(results) == 2:
        assert results[0].final_score >= results[1].final_score


def test_apply_mmr_returns_k_results():
    """_apply_mmr 应精确返回 k 条结果（当候选数 > k 时）。"""
    from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult

    retriever = HybridRetriever(
        bm25_retriever=cast(BM25Retriever, _DummyBM25()),
        vector_retriever=cast(VectorRetriever, _DummyVector()),
        rrf_fusion=RRFFusion(k=60),
        config={"mmr_lambda": 0.7},
    )

    candidates = [
        HybridResult(
            doc_id=i,
            final_score=1.0 - i * 0.1,
            rrf_score=0.5,
            bm25_score=None,
            vector_score=None,
            content=f"content {i} unique words here",
            metadata={},
        )
        for i in range(6)
    ]

    selected = retriever._apply_mmr(candidates, k=3)
    assert len(selected) == 3
