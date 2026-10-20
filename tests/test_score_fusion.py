"""分数级融合与候选加宽的回归测试。"""

from __future__ import annotations

import time
from typing import cast

import pytest

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


def _bm25(doc_id: int, score: float) -> RRFBM25Result:
    return RRFBM25Result(doc_id=doc_id, score=score, content=f"c{doc_id}", metadata={})


def _vector(doc_id: int, score: float) -> VectorResult:
    return VectorResult(doc_id=doc_id, score=score, content=f"c{doc_id}", metadata={})


def test_fuse_scores_follows_the_weight_of_each_route():
    fusion = RRFFusion(k=60)
    bm25 = [_bm25(1, 1.0), _bm25(2, 0.5)]
    vector = [_vector(2, 0.9), _vector(1, 0.1)]

    bm25_heavy = fusion.fuse_scores(bm25, vector, top_k=2, bm25_weight=1.0)
    vector_heavy = fusion.fuse_scores(bm25, vector, top_k=2, bm25_weight=0.0)

    assert [result.doc_id for result in bm25_heavy] == [1, 2]
    assert [result.doc_id for result in vector_heavy] == [2, 1]


def test_fuse_scores_renormalizes_weights_when_one_route_is_empty():
    fused = RRFFusion(k=60).fuse_scores(
        [_bm25(1, 1.0), _bm25(2, 0.2)], [], top_k=2, bm25_weight=0.5
    )

    assert [result.doc_id for result in fused] == [1, 2]
    assert fused[0].bm25_score == 1.0
    assert fused[0].vector_score is None


def test_fuse_scores_breaks_ties_by_best_rank():
    # 两路分数都是常数，归一化后无区分度，应由名次与 id 决定稳定顺序
    fused = RRFFusion(k=60).fuse_scores(
        [_bm25(7, 0.4), _bm25(8, 0.4)],
        [_vector(8, 0.4), _vector(7, 0.4)],
        top_k=2,
        bm25_weight=0.5,
    )

    assert {result.doc_id for result in fused} == {7, 8}
    assert fused[0].doc_id == 7


def test_fuse_scores_returns_nothing_for_empty_input():
    assert RRFFusion(k=60).fuse_scores([], [], top_k=5) == []


def _retriever(config, bm25, vector) -> HybridRetriever:
    return HybridRetriever(
        bm25_retriever=cast(BM25Retriever, bm25),
        vector_retriever=cast(VectorRetriever, vector),
        rrf_fusion=RRFFusion(k=60),
        config=config,
    )


@pytest.mark.asyncio
async def test_hybrid_search_requests_extra_candidates_per_route():
    seen: dict[str, int] = {}

    class _RecordingBM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            seen["bm25"] = k
            return [_bm25(1, 1.0)]

    class _RecordingVector:
        async def search(self, query, k, session_id=None, persona_id=None):
            seen["vector"] = k
            return [_vector(1, 1.0)]

    retriever = _retriever(
        {"recall_candidate_multiplier": 4}, _RecordingBM25(), _RecordingVector()
    )
    await retriever.search("query", k=5)

    assert seen == {"bm25": 20, "vector": 20}


@pytest.mark.asyncio
async def test_hybrid_search_defaults_to_score_fusion():
    now = time.time()

    class _BM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                RRFBM25Result(
                    doc_id=1,
                    score=1.0,
                    content="a",
                    metadata={"importance": 0.5, "create_time": now},
                )
            ]

    class _Vector:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [
                VectorResult(
                    doc_id=2,
                    score=1.0,
                    content="b",
                    metadata={"importance": 0.5, "create_time": now},
                )
            ]

    retriever = _retriever({}, _BM25(), _Vector())
    assert retriever.fusion_mode == "score"

    results = await retriever.search("query", k=2)

    assert {result.doc_id for result in results} == {1, 2}


@pytest.mark.asyncio
async def test_rrf_fusion_mode_remains_selectable():
    class _BM25:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [_bm25(1, 1.0)]

    class _Vector:
        async def search(self, query, k, session_id=None, persona_id=None):
            return [_vector(2, 1.0)]

    retriever = _retriever({"fusion_mode": "rrf"}, _BM25(), _Vector())
    results = await retriever.search("query", k=2)

    assert retriever.fusion_mode == "rrf"
    assert len(results) == 2
