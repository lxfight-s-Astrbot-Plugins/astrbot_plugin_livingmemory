"""Hybrid retrieval inside the graph-memory route."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any

from .graph_keyword_retriever import GraphKeywordRetriever
from .graph_vector_retriever import GraphVectorRetriever
from .rrf_fusion import BM25Result, RRFFusion, VectorResult


@dataclass(slots=True)
class GraphResult:
    """Combined graph-route result mapped to one memory document."""

    doc_id: int
    final_score: float
    rrf_score: float
    keyword_score: float | None
    vector_score: float | None
    content: str
    metadata: dict[str, Any]
    score_breakdown: dict[str, float] | None = None


class GraphRetriever:
    """Fuse graph keyword and graph vector retrieval results."""

    def __init__(
        self,
        keyword_retriever: GraphKeywordRetriever,
        vector_retriever: GraphVectorRetriever,
        rrf_fusion: RRFFusion,
        config: dict[str, Any] | None = None,
    ):
        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.rrf_fusion = rrf_fusion
        self.config = config or {}
        self.decay_rate = float(self.config.get("decay_rate", 0.01))
        self.score_alpha = float(self.config.get("graph_score_alpha", 0.55))
        self.score_beta = float(self.config.get("graph_score_beta", 0.2))
        self.score_gamma = float(self.config.get("graph_score_gamma", 0.15))
        self.score_delta = float(self.config.get("graph_score_delta", 0.1))

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[GraphResult]:
        """Run graph keyword and vector retrieval in parallel."""
        if not query or not query.strip():
            return []

        keyword_results, vector_results = await asyncio.gather(
            self.keyword_retriever.search(query, k, session_id, persona_id),
            self.vector_retriever.search(query, k, session_id, persona_id),
        )

        if not keyword_results and not vector_results:
            return []

        fused = self.rrf_fusion.fuse(
            [
                BM25Result(
                    doc_id=item.doc_id,
                    score=item.score,
                    content=item.content,
                    metadata=item.metadata,
                )
                for item in keyword_results
            ],
            [
                VectorResult(
                    doc_id=item.doc_id,
                    score=item.score,
                    content=item.content,
                    metadata=item.metadata,
                )
                for item in vector_results
            ],
            top_k=k,
        )
        if not fused:
            return []

        keyword_score_map = {item.doc_id: item.score for item in keyword_results}
        vector_score_map = {item.doc_id: item.score for item in vector_results}

        max_rrf = max(item.rrf_score for item in fused) or 1.0
        current_time = time.time()
        results: list[GraphResult] = []

        for item in fused:
            metadata = item.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            importance = max(0.0, min(1.0, float(metadata.get("importance", 0.5))))
            create_time = float(metadata.get("create_time") or current_time)
            last_access_time = float(metadata.get("last_access_time") or 0.0)
            reference_time = max(create_time, last_access_time)
            days_old = max(0.0, (current_time - reference_time) / 86400)
            recency_weight = math.exp(-self.decay_rate * days_old)
            graph_confidence = max(
                0.0,
                min(1.0, float(metadata.get("graph_confidence", 0.7))),
            )
            rrf_normalized = item.rrf_score / max_rrf

            final_score = (
                self.score_alpha * rrf_normalized
                + self.score_beta * importance
                + self.score_gamma * recency_weight
                + self.score_delta * graph_confidence
            )

            results.append(
                GraphResult(
                    doc_id=item.doc_id,
                    final_score=final_score,
                    rrf_score=item.rrf_score,
                    keyword_score=keyword_score_map.get(item.doc_id),
                    vector_score=vector_score_map.get(item.doc_id),
                    content=item.content,
                    metadata=metadata,
                    score_breakdown={
                        "graph_rrf_normalized": round(rrf_normalized, 4),
                        "graph_importance": round(importance, 4),
                        "graph_recency_weight": round(recency_weight, 4),
                        "graph_confidence": round(graph_confidence, 4),
                        "graph_final_score": round(final_score, 4),
                    },
                )
            )

        results.sort(key=lambda item: item.final_score, reverse=True)
        return results[:k]


__all__ = ["GraphRetriever", "GraphResult"]
