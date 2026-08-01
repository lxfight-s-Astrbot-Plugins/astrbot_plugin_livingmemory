"""Manage graph-memory indexing and synchronization."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...storage.graph_store import GraphStore
from ..models.graph_models import GraphEntry
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
        self._rebuild_gate = asyncio.Lock()
        self._rebuild_active = False
        self._rebuild_delta: dict[
            int, tuple[str, dict[str, Any] | None, list | None] | None
        ] = {}

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
        async with self._rebuild_gate:
            if self._rebuild_active:
                self._rebuild_delta[int(source_memory_id)] = (
                    content,
                    metadata,
                    atoms,
                )
                return
        await self._index_memory_now(source_memory_id, content, metadata, atoms)

    async def _index_memory_now(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
        atoms: list | None = None,
    ) -> None:
        await self._delete_memory_now(source_memory_id)

        entries, entry_ids = await self._store_graph_structure(
            source_memory_id,
            content,
            metadata,
            atoms,
        )
        if not entries:
            return

        vector_doc_id = await self.graph_vector_retriever.add_memory_entries(
            [(entry.content, dict(entry.metadata)) for entry in entries]
        )
        await self.graph_store.update_entry_vector_doc_ids(
            {entry_ids[0]: vector_doc_id}
        )

    async def _store_graph_structure(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
        atoms: list | None = None,
    ) -> tuple[list[GraphEntry], list[int]]:
        """Persist graph structure without touching the vector index."""
        extracted = self.graph_extractor.extract(
            source_memory_id, content, metadata, atoms
        )
        if not extracted.entries:
            return [], []

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
        return extracted.entries, entry_ids

    async def delete_memory(self, source_memory_id: int) -> None:
        """Delete graph artifacts belonging to one source memory."""
        async with self._rebuild_gate:
            if self._rebuild_active:
                self._rebuild_delta[int(source_memory_id)] = None
                return
        await self._delete_memory_now(source_memory_id)

    async def _delete_memory_now(self, source_memory_id: int) -> None:
        vector_doc_ids = await self.graph_store.delete_memory(source_memory_id)
        await self.graph_vector_retriever.delete_entries(
            source_memory_id, vector_doc_ids
        )

    async def batch_delete_memories(self, source_memory_ids: list[int]) -> None:
        """Delete graph artifacts in one FAISS bulk operation when supported."""
        if not source_memory_ids:
            return
        async with self._rebuild_gate:
            if self._rebuild_active:
                for source_memory_id in source_memory_ids:
                    self._rebuild_delta[int(source_memory_id)] = None
                return
        memory_vec_map = await self.graph_store.batch_delete_memories(source_memory_ids)
        await self.graph_vector_retriever.delete_entries_batch(memory_vec_map)

    async def rebuild_memories(
        self,
        memories: list[tuple[int, str, dict[str, Any]]],
    ) -> dict[str, int]:
        """Compatibility wrapper for callers that already materialized memories."""

        async def batches():
            yield memories

        return await self.rebuild_memory_batches(batches())

    async def rebuild_memory_batches(
        self,
        memory_batches: AsyncIterable[list[tuple[int, str, dict[str, Any]]]],
        *,
        vector_batch_size: int = 100,
    ) -> dict[str, int]:
        """Build graph data in shadow storage and atomically switch when complete."""
        async with self._rebuild_gate:
            if self._rebuild_active:
                raise RuntimeError("graph rebuild already in progress")
            self._rebuild_active = True
            self._rebuild_delta.clear()

        temp_dir = Path(tempfile.mkdtemp(prefix="livingmemory_graph_rebuild_"))
        shadow_store = GraphStore(str(temp_dir / "graph.db"))
        shadow_manager = GraphMemoryManager(
            shadow_store,
            self.graph_vector_retriever,
            self.graph_extractor,
        )
        new_vector_doc_ids: dict[int, set[int]] = {}
        old_vector_doc_ids = await self.graph_store.list_vector_doc_ids_by_source()
        rebuilt = 0
        skipped = 0
        switched = False

        async def remove_shadow_vectors(
            source_memory_id: int, vector_doc_ids: list[int]
        ) -> None:
            known_ids = new_vector_doc_ids.get(int(source_memory_id), set())
            ids = [
                int(vector_doc_id)
                for vector_doc_id in vector_doc_ids
                if int(vector_doc_id) in known_ids
            ]
            if not ids:
                return
            await self.graph_vector_retriever.delete_entries_batch(
                {int(source_memory_id): ids}
            )
            known_ids.difference_update(ids)
            if not known_ids:
                new_vector_doc_ids.pop(int(source_memory_id), None)

        async def apply_shadow_delta(
            delta: dict[int, tuple[str, dict[str, Any] | None, list | None] | None],
        ) -> None:
            for source_memory_id, payload in delta.items():
                replaced_vector_ids = await shadow_store.delete_memory(source_memory_id)
                await remove_shadow_vectors(source_memory_id, replaced_vector_ids)
                if payload is None:
                    continue
                content, metadata, atoms = payload
                if not content.strip():
                    continue
                entries, entry_ids = await shadow_manager._store_graph_structure(
                    source_memory_id,
                    content,
                    metadata,
                    atoms,
                )
                if not entries:
                    continue
                vector_doc_id = (
                    await self.graph_vector_retriever.add_memory_entries_batch(
                        [[(entry.content, dict(entry.metadata)) for entry in entries]]
                    )
                )[0]
                new_vector_doc_ids.setdefault(int(source_memory_id), set()).add(
                    int(vector_doc_id)
                )
                await shadow_store.update_entry_vector_doc_ids(
                    {entry_ids[0]: int(vector_doc_id)}
                )

        try:
            await shadow_store.initialize()
            async for memories in memory_batches:
                for source_memory_id, content, metadata in memories:
                    if not content.strip():
                        skipped += 1
                        continue
                    entries, _entry_ids = await shadow_manager._store_graph_structure(
                        source_memory_id,
                        content,
                        metadata,
                    )
                    if entries:
                        rebuilt += 1
                    else:
                        skipped += 1

            async for groups in shadow_store.iter_memory_entry_groups(
                max(1, int(vector_batch_size))
            ):
                entry_groups = [entries for _, _, entries in groups]
                vector_doc_ids = (
                    await self.graph_vector_retriever.add_memory_entries_batch(
                        entry_groups
                    )
                )
                if len(vector_doc_ids) != len(groups):
                    raise RuntimeError(
                        "graph vector id count mismatch: "
                        f"ids={len(vector_doc_ids)}, memories={len(groups)}"
                    )
                for (source_memory_id, _, _), vector_doc_id in zip(
                    groups, vector_doc_ids, strict=True
                ):
                    new_vector_doc_ids.setdefault(int(source_memory_id), set()).add(
                        int(vector_doc_id)
                    )
                await shadow_store.update_entry_vector_doc_ids(
                    {
                        representative_entry_id: int(vector_doc_id)
                        for (_, representative_entry_id, _), vector_doc_id in zip(
                            groups, vector_doc_ids, strict=True
                        )
                    }
                )

            while True:
                async with self._rebuild_gate:
                    if self._rebuild_delta:
                        pending_delta = dict(self._rebuild_delta)
                        self._rebuild_delta.clear()
                    else:
                        switch_task = asyncio.create_task(
                            self.graph_store.replace_all_from(
                                str(temp_dir / "graph.db")
                            )
                        )
                        try:
                            await asyncio.shield(switch_task)
                        except asyncio.CancelledError:
                            # Learn whether the transaction committed before
                            # propagating cancellation; never roll back the new
                            # vectors after a successful table switch.
                            await switch_task
                            switched = True
                            self._rebuild_active = False
                            raise
                        switched = True
                        self._rebuild_active = False
                        break
                await apply_shadow_delta(pending_delta)

            if old_vector_doc_ids:
                try:
                    await self.graph_vector_retriever.delete_entries_batch(
                        old_vector_doc_ids
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The graph tables already reference the new generation. Old
                    # vectors are redundant but harmless and can be cleaned later.
                    logger.warning(
                        "图谱已切换，但旧图向量清理失败；保留新一代索引",
                        exc_info=True,
                    )
            return {"rebuilt": rebuilt, "skipped": skipped}
        except BaseException:
            if not switched and new_vector_doc_ids:
                try:
                    await self.graph_vector_retriever.delete_entries_batch(
                        {
                            source_memory_id: sorted(vector_doc_ids)
                            for source_memory_id, vector_doc_ids in new_vector_doc_ids.items()
                        }
                    )
                except Exception:
                    pass
            if not switched:
                async with self._rebuild_gate:
                    pending_delta = dict(self._rebuild_delta)
                    self._rebuild_delta.clear()
                    self._rebuild_active = False
                for source_memory_id, payload in pending_delta.items():
                    if payload is None:
                        await self._delete_memory_now(source_memory_id)
                    else:
                        await self._index_memory_now(source_memory_id, *payload)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


__all__ = ["GraphMemoryManager"]
