"""Keyword retrieval for the graph-memory route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...storage.graph_store import GraphStore
from ..processors.text_processor import TextProcessor
from ..utils import matches_user_id


@dataclass(slots=True)
class GraphKeywordResult:
    """Keyword match aggregated to one source memory."""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


class GraphKeywordRetriever:
    """Retrieve graph-memory candidates with FTS and one-hop expansion."""

    def __init__(
        self,
        graph_store: GraphStore,
        text_processor: TextProcessor,
        config: dict[str, Any] | None = None,
    ):
        self.graph_store = graph_store
        self.text_processor = text_processor
        self.config = config or {}
        self.expansion_limit = int(self.config.get("graph_expansion_limit", 24))

    async def search(
        self,
        query: str,
        limit: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        user_id: str | None = None,
    ) -> list[GraphKeywordResult]:
        """Search the graph route with keyword matching."""
        if not query or not query.strip():
            return []

        tokens = self.text_processor.tokenize(query, remove_stopwords=True)
        if not tokens:
            return []

        escaped_tokens = ['"' + token.replace('"', '""') + '"' for token in tokens]
        fts_query = " OR ".join(escaped_tokens)

        direct_hits = await self.graph_store.search_entries_by_bm25(
            fts_query=fts_query,
            limit=max(limit * 3, 12),
            session_id=session_id,
            persona_id=persona_id,
        )
        matched_nodes = await self.graph_store.search_nodes_by_tokens(
            tokens=tokens,
            limit=max(limit * 3, 12),
        )
        expansion_hits = await self.graph_store.get_entries_for_node_ids(
            node_ids=[item["id"] for item in matched_nodes],
            limit=max(self.expansion_limit, limit * 3),
            session_id=session_id,
            persona_id=persona_id,
        )

        aggregated: dict[int, GraphKeywordResult] = {}

        def merge_hit(hit: dict[str, Any], weight: float, match_source: str) -> None:
            doc_id = int(hit["source_memory_id"])
            weighted_score = max(0.0, min(1.0, float(hit["score"]) * weight))
            hit_metadata = dict(hit.get("metadata") or {})
            hit_metadata["graph_match_source"] = match_source
            hit_metadata["graph_entry_type"] = hit.get("entry_type")
            hit_metadata["graph_relation_type"] = hit.get("relation_type")
            current = aggregated.get(doc_id)
            if current is None or weighted_score > current.score:
                aggregated[doc_id] = GraphKeywordResult(
                    doc_id=doc_id,
                    score=weighted_score,
                    content=str(hit.get("content") or ""),
                    metadata=hit_metadata,
                )
                return
            current.score = min(1.0, current.score + weighted_score * 0.35)
            if "graph_match_source" in current.metadata:
                current.metadata["graph_match_source"] = (
                    f"{current.metadata['graph_match_source']}+{match_source}"
                )

        for hit in direct_hits:
            merge_hit(hit, weight=1.0, match_source="graph_keyword")

        for hit in expansion_hits:
            merge_hit(hit, weight=0.7, match_source="graph_neighbor")

        results = sorted(aggregated.values(), key=lambda item: item.score, reverse=True)
        if user_id is not None:
            results = [r for r in results if matches_user_id(r.metadata, user_id)]
        return results[:limit]


__all__ = ["GraphKeywordRetriever", "GraphKeywordResult"]
