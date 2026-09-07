"""Fuse document-route and graph-route retrieval into one result list."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .graph_retriever import GraphRetriever
from .hybrid_retriever import HybridResult, HybridRetriever
from .route_execution import search_route
from .vector_search import query_embeddings


class DualRouteRetriever:
    """Coordinate document and graph retrieval routes."""

    def __init__(
        self,
        document_retriever: HybridRetriever,
        graph_retriever: GraphRetriever,
        memory_loader: Callable[[int], Awaitable[dict[str, Any] | None]],
        config: dict[str, Any] | None = None,
        memory_batch_loader=None,
    ):
        self.document_retriever = document_retriever
        self.graph_retriever = graph_retriever
        self.memory_loader = memory_loader
        self.memory_batch_loader = memory_batch_loader
        self.config = config or {}
        self.document_route_weight = float(
            self.config.get("document_route_weight", 0.65)
        )
        self.graph_route_weight = float(self.config.get("graph_route_weight", 0.35))
        self.cross_route_bonus = float(self.config.get("cross_route_bonus", 0.08))
        self.dynamic_route_weighting = bool(
            self.config.get("dynamic_route_weighting", True)
        )

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[HybridResult]:
        """Run both retrieval routes and merge their memory candidates."""
        timeout = self.config.get("retrieval_timeout_seconds", 10.0) * 2 + 1
        cache = {}
        token = query_embeddings.set(cache)
        try:
            (doc_results, _), (graph_results, _) = await asyncio.gather(
                search_route(
                    "document",
                    self.document_retriever.search(
                        query, max(k * 2, k), session_id, persona_id
                    ),
                    timeout,
                ),
                search_route(
                    "graph",
                    self.graph_retriever.search(
                        query, max(k * 2, k), session_id, persona_id
                    ),
                    timeout,
                ),
            )
        finally:
            query_embeddings.reset(token)
            for task in cache.values():
                if not task.done():
                    task.cancel()
            if cache:
                await asyncio.gather(*cache.values(), return_exceptions=True)

        if not graph_results:
            return doc_results[:k]
        if not doc_results and not graph_results:
            return []

        document_weight, graph_weight, intent = self._route_weights_for_query(query)

        document_max = (
            max((item.final_score for item in doc_results), default=1.0) or 1.0
        )
        graph_max = (
            max((item.final_score for item in graph_results), default=1.0) or 1.0
        )

        doc_map = {item.doc_id: item for item in doc_results}
        graph_map = {item.doc_id: item for item in graph_results}
        all_doc_ids = set(doc_map) | set(graph_map)

        missing_ids = [
            doc_id
            for doc_id in all_doc_ids
            if doc_id not in doc_map
            or not doc_map[doc_id].content
            or not doc_map[doc_id].metadata
        ]
        loaded = {}
        if missing_ids:
            if self.memory_batch_loader is not None:
                documents = await self.memory_batch_loader(
                    metadata_filters={}, ids=missing_ids, limit=len(missing_ids)
                )
                loaded = {int(document["id"]): document for document in documents}
            else:
                documents = await asyncio.gather(
                    *(self.memory_loader(doc_id) for doc_id in missing_ids)
                )
                loaded = dict(zip(missing_ids, documents))

        merged_results: list[HybridResult] = []
        for doc_id in all_doc_ids:
            doc_result = doc_map.get(doc_id)
            graph_result = graph_map.get(doc_id)

            doc_signal = (
                doc_result.final_score / document_max if doc_result is not None else 0.0
            )
            graph_signal = (
                graph_result.final_score / graph_max
                if graph_result is not None
                else 0.0
            )
            route_bonus = (
                self.cross_route_bonus
                if doc_result is not None and graph_result is not None
                else 0.0
            )

            memory_content = doc_result.content if doc_result is not None else ""
            memory_metadata = (
                dict(doc_result.metadata)
                if doc_result is not None and isinstance(doc_result.metadata, dict)
                else {}
            )

            if not memory_content or not memory_metadata:
                memory = loaded.get(doc_id)
                if not memory:
                    continue
                memory_content = str(memory.get("text") or memory_content)
                raw_metadata = memory.get("metadata") or memory_metadata
                memory_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

            final_score = min(
                1.0,
                document_weight * doc_signal
                + graph_weight * graph_signal
                + route_bonus,
            )

            score_breakdown: dict[str, float] = {}
            if doc_result and doc_result.score_breakdown:
                score_breakdown.update(doc_result.score_breakdown)
            if graph_result and graph_result.score_breakdown:
                score_breakdown.update(graph_result.score_breakdown)
            score_breakdown.update(
                {
                    "document_route_score": round(doc_signal, 4),
                    "graph_route_score": round(graph_signal, 4),
                    "document_route_weight": round(document_weight, 4),
                    "graph_route_weight": round(graph_weight, 4),
                    "cross_route_bonus": round(route_bonus, 4),
                    "dual_route_final_score": round(final_score, 4),
                }
            )
            if intent:
                score_breakdown["query_intent"] = intent
            if doc_result is not None:
                score_breakdown["document_keyword_score"] = round(
                    float(doc_result.bm25_score or 0.0),
                    4,
                )
                score_breakdown["document_vector_score"] = round(
                    float(doc_result.vector_score or 0.0),
                    4,
                )
            if graph_result is not None:
                score_breakdown["graph_keyword_score"] = round(
                    float(graph_result.keyword_score or 0.0),
                    4,
                )
                score_breakdown["graph_vector_score"] = round(
                    float(graph_result.vector_score or 0.0),
                    4,
                )

            merged_results.append(
                HybridResult(
                    doc_id=doc_id,
                    final_score=final_score,
                    rrf_score=max(
                        doc_result.rrf_score if doc_result is not None else 0.0,
                        graph_result.rrf_score if graph_result is not None else 0.0,
                    ),
                    bm25_score=doc_result.bm25_score
                    if doc_result is not None
                    else None,
                    vector_score=(
                        doc_result.vector_score if doc_result is not None else None
                    ),
                    content=memory_content,
                    metadata=memory_metadata,
                    score_breakdown=score_breakdown,
                )
            )

        merged_results.sort(key=lambda item: item.final_score, reverse=True)
        return merged_results[:k]

    def _route_weights_for_query(self, query: str) -> tuple[float, float, str]:
        """Adjust document/graph weights with lightweight query intent rules."""
        base_document = self.document_route_weight
        base_graph = self.graph_route_weight
        if not self.dynamic_route_weighting:
            return base_document, base_graph, "fixed"

        normalized = query.casefold()
        relation_terms = (
            "谁",
            "和谁",
            "关系",
            "认识",
            "朋友",
            "同事",
            "家人",
            "父母",
            "妈妈",
            "爸爸",
            "老师",
            "同学",
            "partner",
            "friend",
            "relationship",
            "with whom",
        )
        temporal_terms = (
            "上次",
            "昨天",
            "前天",
            "刚才",
            "之前",
            "什么时候",
            "哪天",
            "最近",
            "last time",
            "yesterday",
            "recently",
            "when",
        )
        factual_terms = (
            "是什么",
            "什么是",
            "解释",
            "定义",
            "怎么",
            "如何",
            "why",
            "what is",
            "explain",
            "define",
            "how to",
        )

        relation_hit = any(term in normalized for term in relation_terms)
        temporal_hit = any(term in normalized for term in temporal_terms)
        factual_hit = any(term in normalized for term in factual_terms)

        document_weight = base_document
        graph_weight = base_graph
        intent = "default"

        if relation_hit:
            graph_weight += 0.2
            document_weight -= 0.2
            intent = "relationship"
        if temporal_hit:
            graph_weight += 0.1
            document_weight -= 0.1
            intent = "temporal" if intent == "default" else f"{intent}+temporal"
        if factual_hit and not relation_hit:
            document_weight += 0.15
            graph_weight -= 0.15
            intent = "factual" if intent == "default" else f"{intent}+factual"

        document_weight = max(0.15, min(0.9, document_weight))
        graph_weight = max(0.1, min(0.85, graph_weight))
        total = document_weight + graph_weight
        if total <= 0:
            return base_document, base_graph, "fixed"
        return document_weight / total, graph_weight / total, intent


__all__ = ["DualRouteRetriever"]
