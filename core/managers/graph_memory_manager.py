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
        atoms: list | None = None,
    ) -> None:
        """Rebuild graph artifacts for one source memory.

        When atoms are provided, each atom independently contributes
        nodes/edges/entries with per-atom confidence scores.
        """
        await self.delete_memory(source_memory_id)

        extracted = self.graph_extractor.extract(
            source_memory_id, content, metadata, atoms
        )
        if not extracted.entries:
            return

        node_key_to_id = await self.graph_store.upsert_nodes(extracted.nodes)

        edge_key_to_id = await self.graph_store.add_edges(
            extracted.edges,
            node_key_to_id,
        )

        entry_ids = await self.graph_store.add_entries(
            extracted.entries,
            node_key_to_id,
            edge_key_to_id,
        )
        if len(entry_ids) != len(extracted.entries):
            raise RuntimeError(
                "graph entry id count mismatch: "
                f"ids={len(entry_ids)}, entries={len(extracted.entries)}"
            )
        entry_vector_doc_ids: dict[int, int] = {}
        try:
            vector_doc_ids = await self.graph_vector_retriever.add_entries(
                [(entry.content, dict(entry.metadata)) for entry in extracted.entries]
            )
            if len(vector_doc_ids) != len(entry_ids):
                raise RuntimeError(
                    "graph vector id count mismatch: "
                    f"ids={len(vector_doc_ids)}, entries={len(entry_ids)}"
                )
            entry_vector_doc_ids.update(zip(entry_ids, vector_doc_ids, strict=True))
        finally:
            await self.graph_store.update_entry_vector_doc_ids(entry_vector_doc_ids)

    async def delete_memory(self, source_memory_id: int) -> None:
        """Delete graph artifacts belonging to one source memory."""
        vector_doc_ids = await self.graph_store.delete_memory(source_memory_id)
        await self.graph_vector_retriever.delete_entries(
            source_memory_id, vector_doc_ids
        )

    async def batch_delete_memories(self, source_memory_ids: list[int]) -> None:
        """Delete graph artifacts in one FAISS bulk operation when supported."""
        if not source_memory_ids:
            return
        memory_vec_map = await self.graph_store.batch_delete_memories(source_memory_ids)
        await self.graph_vector_retriever.delete_entries_batch(memory_vec_map)


__all__ = ["GraphMemoryManager"]
