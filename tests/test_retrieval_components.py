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
