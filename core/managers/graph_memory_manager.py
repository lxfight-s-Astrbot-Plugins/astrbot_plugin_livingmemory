"""Manage graph-memory indexing and synchronization."""

from __future__ import annotations

from typing import Any

from ...storage.graph_store import GraphStore
from ..processors.graph_extractor import GraphExtractor
from ..retrieval.graph_vector_retriever import GraphVectorRetriever


class GraphMemoryManager:
    """Synchronize graph-memory artifacts with the document memory store."""

    def __init__(
        self,
        graph_store: GraphStore,
        graph_vector_retriever: GraphVectorRetriever,
        graph_extractor: GraphExtractor,
    ):
        self.graph_store = graph_store
        self.graph_vector_retriever = graph_vector_retriever
        self.graph_extractor = graph_extractor

    async def index_memory(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Rebuild graph artifacts for one source memory."""
        await self.delete_memory(source_memory_id)

        extracted = self.graph_extractor.extract(source_memory_id, content, metadata)
        if not extracted.entries:
            return

        node_key_to_id: dict[str, int] = {}
        for node in extracted.nodes:
            node_key_to_id[node.node_key] = await self.graph_store.upsert_node(node)

        edge_key_to_id: dict[str, int] = {}
        for edge in extracted.edges:
            edge_key_to_id[edge.edge_key] = await self.graph_store.add_edge(
                edge,
                node_key_to_id,
            )

        for entry in extracted.entries:
            edge_id = None
            if entry.relation_type and len(entry.node_keys) >= 2:
                edge_key = (
                    f"{entry.node_keys[0]}|{entry.relation_type}|"
                    f"{entry.node_keys[1]}|{source_memory_id}"
                )
                edge_id = edge_key_to_id.get(edge_key)
            entry_id = await self.graph_store.add_entry(
                entry,
                node_key_to_id,
                edge_id=edge_id,
            )
            vector_doc_id = await self.graph_vector_retriever.add_entry(
                entry.content,
                dict(entry.metadata),
            )
            await self.graph_store.update_entry_vector_doc_id(entry_id, vector_doc_id)

    async def delete_memory(self, source_memory_id: int) -> None:
        """Delete graph artifacts belonging to one source memory."""
        vector_doc_ids = await self.graph_store.delete_memory(source_memory_id)
        for vector_doc_id in vector_doc_ids:
            await self.graph_vector_retriever.delete_entry(vector_doc_id)


__all__ = ["GraphMemoryManager"]
