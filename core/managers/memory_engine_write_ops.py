"""
MemoryEngine 的 MemoryEngineWriteOpsMixin 拆分模块
自动从 core/managers/memory_engine.py 拆分，保持行为不变
"""

import asyncio
import copy
import json
import time
from typing import Any

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ..models.memory_atom import AtomStatus, AtomType, DecayType, MemoryAtom
from ..retrieval.hybrid_retriever import HybridResult
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float

import aiosqlite
from collections import OrderedDict


class MemoryEngineWriteOpsMixin:
    """MemoryEngine 拆分模块：MemoryEngineWriteOpsMixin

    下述类级注解声明本模块依赖的宿主共享状态（由 MemoryEngine.__init__ 赋值），
    仅作类型约束，不在此赋默认值。
    """

    # 宿主共享状态契约
    db_connection: aiosqlite.Connection | None
    config: dict[str, Any]
    faiss_db: Any
    atom_enabled: bool
    atom_store: AtomStore | None
    hybrid_retriever: Any | None
    dual_route_retriever: Any | None
    graph_memory_manager: Any | None
    _search_cache_enabled: bool
    _search_cache_ttl: float
    _search_cache_max_size: int
    _search_cache_generation: int
    _search_cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]]

    def _normalize_cache_query(self, query: str) -> str:
        return " ".join(query.casefold().split())

    def _search_cache_key(
        self,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> tuple[Any, ...]:
        return (
            self._search_cache_generation,
            self._normalize_cache_query(query),
            int(k),
            session_id or "",
            persona_id or "",
            bool(self.dual_route_retriever is not None),
            round(float(self.config.get("document_route_weight", 0.65)), 4),
            round(float(self.config.get("graph_route_weight", 0.35)), 4),
            int(self.config.get("graph_expansion_hops", 1)),
            round(float(self.config.get("min_importance_for_retrieval", 0.0)), 4),
            round(float(self.config.get("min_similarity_for_retrieval", 0.0)), 4),
            int(self.config.get("recent_memory_count", 2)),
            int(self.config.get("recent_memory_max_age_hours", 72)),
            str(self.config.get("memory_type_filter", "all")),
            bool(self.config.get("rerank_enabled", False)),
            int(self.config.get("rerank_candidates", 20)),
            str(self.config.get("fusion_mode", "score")),
            round(float(self.config.get("fusion_bm25_weight", 0.5)), 4),
            int(self.config.get("recall_candidate_multiplier", 3)),
        )

    def _filter_by_retrieval_policy(
        self, results: list[HybridResult]
    ) -> list[HybridResult]:
        importance_threshold = clamp_float(
            self.config.get("min_importance_for_retrieval"), default=0.0
        )
        similarity_threshold = clamp_float(
            self.config.get("min_similarity_for_retrieval"), default=0.0
        )
        event_only = self.config.get("memory_type_filter", "all") == "event_only"
        event_atom_types = {"episodic", "planned", "factual"}
        filtered: list[HybridResult] = []

        for result in results:
            metadata = getattr(result, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            if str(metadata.get("status") or "active") != "active":
                continue
            if (
                importance_threshold > 0
                and clamp_float(metadata.get("importance"), default=0.5)
                < importance_threshold
            ):
                continue

            signals: list[float] = []
            vector_score = getattr(result, "vector_score", None)
            if vector_score is not None:
                signals.append(clamp_float(vector_score, default=0.0))
            breakdown = getattr(result, "score_breakdown", None)
            if isinstance(breakdown, dict):
                for key in ("document_vector_score", "graph_vector_score"):
                    if key in breakdown:
                        signals.append(clamp_float(breakdown[key], default=0.0))
            if (
                similarity_threshold > 0
                and signals
                and max(signals) < similarity_threshold
            ):
                continue

            atom_types = metadata.get("atom_types")
            if event_only and isinstance(atom_types, list) and atom_types:
                normalized_types = {str(value).casefold() for value in atom_types}
                if normalized_types.isdisjoint(event_atom_types):
                    continue
            filtered.append(result)
        return filtered

    async def _get_recent_memory_results(
        self,
        count: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> list[HybridResult]:
        if count <= 0 or self.db_connection is None:
            return []

        conditions = [
            "COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'"
        ]
        params: list[Any] = []
        if session_id is not None:
            conditions.append("json_extract(metadata, '$.session_id') = ?")
            params.append(session_id)
        if persona_id is not None:
            conditions.append("json_extract(metadata, '$.persona_id') = ?")
            params.append(persona_id)
        max_age_hours = max(0, int(self.config.get("recent_memory_max_age_hours", 72)))
        if max_age_hours > 0:
            # 表达式与 idx_doc_create_time 逐字匹配，可走索引范围扫描
            conditions.append(
                "COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) >= ?"
            )
            params.append(time.time() - max_age_hours * 3600)
        params.append(max(count * 3, count))

        cursor = await self.db_connection.execute(
            "SELECT id, text, metadata FROM documents INDEXED BY idx_doc_create_time WHERE "
            + " AND ".join(conditions)
            + " ORDER BY COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) DESC, id DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        recent = [
            HybridResult(
                doc_id=int(row["id"]),
                final_score=1.0,
                rrf_score=0.0,
                bm25_score=None,
                vector_score=None,
                content=str(row["text"] or ""),
                metadata=safe_json_dict(row["metadata"]),
                score_breakdown={"recent_memory": 1.0},
            )
            for row in rows
        ]
        return self._filter_by_retrieval_policy(recent)[:count]

    async def _merge_recent_memories(
        self,
        results: list[HybridResult],
        k: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> list[HybridResult]:
        recent_count = min(max(0, int(self.config.get("recent_memory_count", 2))), k)
        if recent_count <= 0:
            return results[:k]

        recent = await self._get_recent_memory_results(
            recent_count, session_id, persona_id
        )
        if not recent:
            return results[:k]

        selected = list(results[: max(0, k - recent_count)])
        selected_ids = {result.doc_id for result in selected}
        for result in recent:
            if result.doc_id not in selected_ids:
                selected.append(result)
                selected_ids.add(result.doc_id)
        for result in results:
            if len(selected) >= k:
                break
            if result.doc_id not in selected_ids:
                selected.append(result)
                selected_ids.add(result.doc_id)
        return selected[:k]

    def _get_cached_search_results(
        self,
        cache_key: tuple[Any, ...],
    ) -> list[HybridResult] | None:
        if (
            not self._search_cache_enabled
            or self._search_cache_ttl <= 0
            or self._search_cache_max_size <= 0
        ):
            return None

        cached = self._search_cache.get(cache_key)
        if cached is None:
            return None

        cached_at, results = cached
        if time.time() - cached_at > self._search_cache_ttl:
            self._search_cache.pop(cache_key, None)
            return None

        self._search_cache.move_to_end(cache_key)
        return copy.deepcopy(results)

    def _set_cached_search_results(
        self,
        cache_key: tuple[Any, ...],
        results: list[HybridResult],
    ) -> None:
        if (
            not self._search_cache_enabled
            or self._search_cache_ttl <= 0
            or self._search_cache_max_size <= 0
        ):
            return

        self._search_cache[cache_key] = (time.time(), copy.deepcopy(results))
        self._search_cache.move_to_end(cache_key)
        while len(self._search_cache) > self._search_cache_max_size:
            self._search_cache.popitem(last=False)

    def _invalidate_search_cache(self) -> None:
        """Invalidate cached retrieval results after memory writes."""
        self._search_cache_generation += 1
        self._search_cache.clear()

    async def _delete_document_indexes_for_batch(self, memory_ids: list[int]) -> int:
        if not memory_ids or self.db_connection is None:
            return 0

        placeholders = ",".join("?" * len(memory_ids))
        await self.db_connection.execute(
            f"DELETE FROM livingmemory_memories_fts WHERE doc_id IN ({placeholders})",
            memory_ids,
        )

        cursor = await self.db_connection.execute(
            f"SELECT id, doc_id FROM documents WHERE id IN ({placeholders})",
            memory_ids,
        )
        uuid_rows = await cursor.fetchall()
        for row in uuid_rows:
            uuid_doc_id = row["doc_id"]
            if not uuid_doc_id:
                continue
            try:
                await self.faiss_db.delete(uuid_doc_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[批量删除] FAISS 删除失败 (id={row['id']})",
                    exc_info=True,
                )

        cursor = await self.db_connection.execute(
            f"DELETE FROM documents WHERE id IN ({placeholders})",
            memory_ids,
        )
        await self.db_connection.execute(
            f"DELETE FROM memory_sources WHERE memory_id IN ({placeholders})",
            memory_ids,
        )
        await self.db_connection.commit()
        return int(cursor.rowcount or 0)

    async def _delete_graph_and_atoms_for_batch(self, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        if self.graph_memory_manager is not None:
            deleted = await self.graph_memory_manager.batch_delete_memories(memory_ids)
            if deleted is False:
                raise RuntimeError("Graph deletion deferred until rebuild completes")
        if self.atom_store is not None:
            await self.atom_store.batch_delete_by_parent(memory_ids)

    async def _drop_legacy_documents_fts_triggers(self):
        if self.db_connection is None:
            return

        cursor = await self.db_connection.execute("""
            SELECT name FROM sqlite_master
            WHERE type='trigger' AND tbl_name='documents'
              AND sql LIKE '%documents_fts%'
        """)
        rows = await cursor.fetchall()
        for row in rows:
            trigger_name = row[0]
            await self.db_connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
            logger.warning(f"已清理旧 LivingMemory FTS 触发器: {trigger_name}")
