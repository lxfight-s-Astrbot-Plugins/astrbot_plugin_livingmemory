"""Vector retrieval for the graph-memory route."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .vector_retriever import delete_faiss_documents_by_ids


@dataclass(slots=True)
class GraphVectorResult:
    """Vector match aggregated to one source memory."""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


class GraphVectorRetriever:
    """Wrap a vector store dedicated to graph-memory entries."""

    _MAX_MEMORY_VECTOR_CHARS = 4000

    def __init__(self, faiss_db, config: dict[str, Any] | None = None):
        self.faiss_db = faiss_db
        self.config = config or {}

    def _coerce_metadata(self, raw_metadata: Any) -> dict[str, Any]:
        if isinstance(raw_metadata, dict):
            return raw_metadata
        if isinstance(raw_metadata, str):
            try:
                parsed = json.loads(raw_metadata)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    async def add_entry(self, content: str, metadata: dict[str, Any]) -> int:
        """Insert one graph entry into the vector database."""
        return await self.faiss_db.insert(content=content, metadata=metadata)

    async def add_entries(self, entries: list[tuple[str, dict[str, Any]]]) -> list[int]:
        """Insert one source memory as one aggregated graph vector."""
        if not entries:
            return []

        return [await self.add_memory_entries(entries)]

    @classmethod
    def _aggregate_memory_entries(
        cls,
        entries: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]]:
        """Build one bounded vector document from a memory's graph entries."""
        if not entries:
            raise ValueError("graph entries cannot be empty")

        unique_contents = list(
            dict.fromkeys(content.strip() for content, _ in entries if content.strip())
        )
        if not unique_contents:
            raise ValueError("graph entries contain no searchable content")

        content = "\n".join(unique_contents)
        if len(content) > cls._MAX_MEMORY_VECTOR_CHARS:
            content = content[: cls._MAX_MEMORY_VECTOR_CHARS]

        metadata = dict(entries[0][1])
        metadata.update(
            {
                "graph_vector_granularity": "memory",
                "graph_entry_count": len(entries),
            }
        )
        return content, metadata

    async def add_memory_entries(
        self,
        entries: list[tuple[str, dict[str, Any]]],
    ) -> int:
        """Insert one source memory's aggregated graph vector."""
        ids = await self.add_memory_entries_batch([entries])
        if len(ids) != 1:
            raise RuntimeError(f"expected one graph vector id, got {len(ids)}")
        return ids[0]

    async def add_memory_entries_batch(
        self,
        memories: list[list[tuple[str, dict[str, Any]]]],
    ) -> list[int]:
        """Insert many source memories with one FAISS persistence operation."""
        if not memories:
            return []

        aggregated = [self._aggregate_memory_entries(entries) for entries in memories]
        contents = [content for content, _ in aggregated]
        metadatas = [metadata for _, metadata in aggregated]

        insert_batch = getattr(self.faiss_db, "insert_batch", None)
        if callable(insert_batch):
            return await insert_batch(
                contents=contents,
                metadatas=metadatas,
            )

        # Compatibility with older AstrBot versions without insert_batch.
        return [
            await self.add_entry(content, metadata)
            for content, metadata in zip(contents, metadatas, strict=True)
        ]

    async def clear_all(self, known_vector_doc_ids: list[int]) -> None:
        """Clear the dedicated graph-vector store with one save when supported."""
        delete_documents = getattr(self.faiss_db, "delete_documents", None)
        if callable(delete_documents):
            await delete_documents(metadata_filters={})
            return

        await self.delete_entries_batch({0: known_vector_doc_ids})

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[GraphVectorResult]:
        """Search graph entries through vector similarity."""
        if not query or not query.strip():
            return []

        metadata_filters: dict[str, Any] = {}
        if session_id is not None:
            metadata_filters["session_id"] = session_id
        if persona_id is not None:
            metadata_filters["persona_id"] = persona_id

        fetch_k = k * 2 if metadata_filters else k
        raw_results = await self.faiss_db.retrieve(
            query=query,
            k=k,
            fetch_k=fetch_k,
            rerank=False,
            metadata_filters=metadata_filters if metadata_filters else None,
        )

        results: list[GraphVectorResult] = []
        for result in raw_results:
            data = result.data
            metadata = self._coerce_metadata(data.get("metadata"))
            source_memory_id = metadata.get("source_memory_id")
            if source_memory_id is None:
                continue
            results.append(
                GraphVectorResult(
                    doc_id=int(source_memory_id),
                    score=float(result.similarity),
                    content=str(data.get("text") or ""),
                    metadata=metadata,
                )
            )
        return results

    async def _get_uuid_from_id(self, vector_doc_id: int) -> str | None:
        """Resolve the internal UUID used by the underlying vector store."""
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return None
        return docs[0].get("doc_id")

    async def delete_entry(self, vector_doc_id: int) -> bool:
        """Delete one graph entry from the vector store."""
        uuid_doc_id = await self._get_uuid_from_id(vector_doc_id)
        if not uuid_doc_id:
            return False
        await self.faiss_db.delete(uuid_doc_id)
        return True

    async def delete_entries(
        self,
        source_memory_id: int,
        vector_doc_ids: list[int],
    ) -> None:
        """Delete one source memory's graph vectors with one FAISS save."""
        if not vector_doc_ids:
            return

        delete_documents = getattr(self.faiss_db, "delete_documents", None)
        if callable(delete_documents):
            await delete_documents(
                metadata_filters={"source_memory_id": source_memory_id}
            )
            return

        # Compatibility with older AstrBot versions without bulk deletion.
        for vector_doc_id in vector_doc_ids:
            await self.delete_entry(vector_doc_id)

    async def delete_entries_batch(
        self,
        entries_by_source: dict[int, list[int]],
    ) -> None:
        """Delete several source memories with one FAISS save when supported."""
        vector_doc_ids = [
            vector_doc_id
            for source_ids in entries_by_source.values()
            for vector_doc_id in source_ids
        ]
        if not vector_doc_ids:
            return

        deleted_ids = await delete_faiss_documents_by_ids(
            self.faiss_db, vector_doc_ids
        )
        if deleted_ids is not None:
            if len(deleted_ids) != len(set(vector_doc_ids)):
                missing = sorted(set(vector_doc_ids) - set(deleted_ids))
                raise RuntimeError(f"批量图向量删除未找到文档: {missing}")
            return

        for source_memory_id, source_ids in entries_by_source.items():
            await self.delete_entries(source_memory_id, source_ids)

    async def update_metadata(
        self, vector_doc_id: int, metadata: dict[str, Any]
    ) -> bool:
        """Update graph entry metadata stored inside the vector-doc storage."""
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return False

        current_doc = docs[0]
        merged_metadata = dict(self._coerce_metadata(current_doc.get("metadata")))
        merged_metadata.update(metadata)
        async with (
            self.faiss_db.document_storage.get_session() as session,
            session.begin(),
        ):
            from sqlalchemy import text

            await session.execute(
                text("UPDATE documents SET metadata = :metadata WHERE id = :id"),
                {
                    "metadata": json.dumps(merged_metadata, ensure_ascii=False),
                    "id": vector_doc_id,
                },
            )
        return True


__all__ = ["GraphVectorRetriever", "GraphVectorResult"]
