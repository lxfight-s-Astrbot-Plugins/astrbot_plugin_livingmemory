"""
MemoryEngine 的 MemoryEngineWriteOpsMixin 拆分模块
自动从 core/managers/memory_engine.py 拆分，保持行为不变
"""

from typing import Any
import asyncio
from ..models.memory_atom import AtomStatus, AtomType, DecayType, MemoryAtom
from ..utils.number_utils import clamp_float
import copy
from ..retrieval.hybrid_retriever import HybridResult
import json
from astrbot.api import logger
import time


class MemoryEngineWriteOpsMixin:
    """MemoryEngine 拆分模块：MemoryEngineWriteOpsMixin"""
    async def _create_write_ops_table(self) -> None:
        """Create the resumable write-operation log."""
        if self.db_connection is None:
            return
        await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS memory_write_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type TEXT NOT NULL,
                memory_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                step TEXT NOT NULL DEFAULT 'started',
                payload TEXT DEFAULT '{}',
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_write_ops_status
            ON memory_write_ops(status, updated_at)
        """)
        await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_write_ops_memory
            ON memory_write_ops(memory_id, op_type)
        """)

    async def _start_write_op(
        self,
        op_type: str,
        payload: dict[str, Any] | None = None,
        memory_id: int | None = None,
    ) -> int | None:
        """Record the beginning of a multi-store write operation."""
        if self.db_connection is None:
            return None
        now = time.time()
        try:
            cursor = await self.db_connection.execute(
                """
                INSERT INTO memory_write_ops(
                    op_type, memory_id, status, step, payload,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', 'started', ?, ?, ?)
                """,
                (
                    op_type,
                    memory_id,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await self.db_connection.commit()
            return int(cursor.lastrowid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[MemoryEngine] 写操作日志创建失败", exc_info=True)
            return None

    async def _advance_write_op(
        self,
        op_id: int | None,
        step: str,
        *,
        status: str = "pending",
        memory_id: int | None = None,
        error: str | None = None,
        payload_patch: dict[str, Any] | None = None,
    ) -> None:
        """Advance a write-operation log entry."""
        if op_id is None or self.db_connection is None:
            return

        try:
            if status == "completed":
                error = None
            current_payload: dict[str, Any] = {}
            if payload_patch:
                cursor = await self.db_connection.execute(
                    "SELECT payload FROM memory_write_ops WHERE id = ?",
                    (op_id,),
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    try:
                        loaded = json.loads(row[0])
                        current_payload = loaded if isinstance(loaded, dict) else {}
                    except (json.JSONDecodeError, TypeError):
                        current_payload = {}
                current_payload.update(payload_patch)

            fields = ["step = ?", "updated_at = ?"]
            params: list[Any] = [step, time.time()]
            if memory_id is not None:
                fields.append("memory_id = ?")
                params.append(memory_id)
            if error is not None:
                fields.append("error = ?")
                params.append(error[:1000])
                fields.append("retry_count = retry_count + 1")
                # 达到重试上限后转为终态 failed，避免待修复记录永久滞留。
                fields.append(
                    "status = CASE WHEN retry_count + 1 >= ? THEN 'failed' ELSE ? END"
                )
                params.append(self._write_op_max_retries)
                params.append(status)
            else:
                fields.append("status = ?")
                params.append(status)
                if status == "completed":
                    fields.append("error = NULL")
            if payload_patch:
                fields.append("payload = ?")
                params.append(json.dumps(current_payload, ensure_ascii=False))
            params.append(op_id)
            await self.db_connection.execute(
                f"UPDATE memory_write_ops SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            await self.db_connection.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[MemoryEngine] 写操作日志更新失败", exc_info=True)

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
            if similarity_threshold > 0 and signals and max(signals) < similarity_threshold:
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
        max_age_hours = max(
            0, int(self.config.get("recent_memory_max_age_hours", 72))
        )
        if max_age_hours > 0:
            conditions.append(
                "CAST(json_extract(metadata, '$.create_time') AS REAL) >= ?"
            )
            params.append(time.time() - max_age_hours * 3600)
        params.append(max(count * 3, count))

        cursor = await self.db_connection.execute(
            "SELECT id, text, metadata FROM documents WHERE "
            + " AND ".join(conditions)
            + " ORDER BY CAST(json_extract(metadata, '$.create_time') AS REAL) DESC, id DESC LIMIT ?",
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
                metadata=self._safe_json_dict(row["metadata"]),
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

    def _serialize_atom_for_repair(self, atom: Any) -> dict[str, Any]:
        """Convert a MemoryAtom-like object into JSON-safe repair payload."""
        atom_type = getattr(atom, "atom_type", AtomType.UNKNOWN)
        decay_type = getattr(atom, "decay_type", DecayType.EXPONENTIAL)
        status = getattr(atom, "status", AtomStatus.ACTIVE)
        return {
            "parent_memory_id": int(getattr(atom, "parent_memory_id", 0) or 0),
            "atom_type": getattr(atom_type, "value", str(atom_type)),
            "content": str(getattr(atom, "content", "")),
            "entities": list(getattr(atom, "entities", []) or []),
            "importance": float(getattr(atom, "importance", 0.5) or 0.5),
            "confidence": float(getattr(atom, "confidence", 0.7) or 0.7),
            "created_at": float(
                getattr(atom, "created_at", time.time()) or time.time()
            ),
            "last_accessed_at": float(
                getattr(atom, "last_accessed_at", time.time()) or time.time()
            ),
            "last_reinforced_at": getattr(atom, "last_reinforced_at", None),
            "event_time": getattr(atom, "event_time", None),
            "ttl_days": float(getattr(atom, "ttl_days", 30.0) or 30.0),
            "expires_at": float(getattr(atom, "expires_at", 0.0) or 0.0),
            "status": getattr(status, "value", str(status)),
            "reinforcement_count": int(getattr(atom, "reinforcement_count", 0) or 0),
            "decay_type": getattr(decay_type, "value", str(decay_type)),
            "session_id": getattr(atom, "session_id", None),
            "persona_id": getattr(atom, "persona_id", None),
            "metadata": dict(getattr(atom, "metadata", {}) or {}),
        }

    def _deserialize_atom_from_repair(
        self,
        payload: dict[str, Any],
        parent_memory_id: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> MemoryAtom | None:
        """Rebuild a MemoryAtom from repair payload."""
        content = str(payload.get("content") or "")
        if not content.strip():
            return None

        try:
            atom_type = AtomType(payload.get("atom_type") or AtomType.UNKNOWN.value)
        except ValueError:
            atom_type = AtomType.UNKNOWN
        try:
            decay_type = DecayType(
                payload.get("decay_type") or DecayType.EXPONENTIAL.value
            )
        except ValueError:
            decay_type = DecayType.EXPONENTIAL
        try:
            status = AtomStatus(payload.get("status") or AtomStatus.ACTIVE.value)
        except ValueError:
            status = AtomStatus.ACTIVE

        return MemoryAtom(
            parent_memory_id=parent_memory_id,
            atom_type=atom_type,
            content=content,
            entities=[str(item) for item in payload.get("entities", []) if item],
            importance=float(payload.get("importance", 0.5) or 0.5),
            confidence=float(payload.get("confidence", 0.7) or 0.7),
            created_at=float(payload.get("created_at", time.time()) or time.time()),
            last_accessed_at=float(
                payload.get("last_accessed_at", time.time()) or time.time()
            ),
            last_reinforced_at=payload.get("last_reinforced_at"),
            event_time=payload.get("event_time"),
            ttl_days=float(payload.get("ttl_days", 30.0) or 30.0),
            expires_at=float(payload.get("expires_at", 0.0) or 0.0),
            status=status,
            reinforcement_count=int(payload.get("reinforcement_count", 0) or 0),
            decay_type=decay_type,
            session_id=payload.get("session_id") or session_id,
            persona_id=payload.get("persona_id") or persona_id,
            metadata=dict(payload.get("metadata") or {}),
        )

    async def _repair_incomplete_write_ops(self) -> int:
        """Best-effort replay for incomplete add/delete operations."""
        if self.db_connection is None:
            return 0

        try:
            cursor = await self.db_connection.execute(
                """
                SELECT id, op_type, memory_id, status, step, payload, retry_count
                FROM memory_write_ops
                WHERE status IN ('pending', 'needs_repair')
                  AND retry_count < ?
                ORDER BY id ASC
                LIMIT 25
                """,
                (self._write_op_max_retries,),
            )
            rows = await cursor.fetchall()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[MemoryEngine] 读取待修复写操作失败", exc_info=True)
            return 0

        repaired = 0
        for row in rows:
            payload = self._safe_json_dict(row["payload"])
            try:
                op_type = row["op_type"]
                memory_id = row["memory_id"]
                if op_type == "add":
                    ok = await self._repair_add_write_op(
                        int(row["id"]),
                        int(memory_id) if memory_id is not None else None,
                        payload,
                    )
                elif op_type == "delete":
                    ok = await self._repair_delete_write_op(
                        int(row["id"]),
                        int(memory_id) if memory_id is not None else None,
                    )
                elif op_type == "batch_delete":
                    ok = await self._repair_batch_delete_write_op(
                        int(row["id"]),
                        payload,
                    )
                else:
                    ok = False
                repaired += 1 if ok else 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"[MemoryEngine] 修复写操作失败 (op_id={row['id']})",
                    exc_info=True,
                )
                await self._advance_write_op(
                    int(row["id"]),
                    str(row["step"] or "repair_failed"),
                    status="needs_repair",
                    error=str(e),
                )

        if repaired:
            logger.info(f"[MemoryEngine] 已修复 {repaired} 个未完成写操作")
            self._invalidate_search_cache()
        return repaired

    async def _repair_add_write_op(
        self,
        op_id: int,
        memory_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        if memory_id is None:
            await self._advance_write_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for add repair",
            )
            return False

        memory = await self.get_memory(int(memory_id))
        if memory is None:
            await self._advance_write_op(
                op_id,
                "source_missing",
                status="failed",
                memory_id=int(memory_id),
                error="source document missing",
            )
            return False

        metadata = memory.get("metadata") or payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = self._safe_json_dict(metadata)
        if metadata.get("has_source") and not await self.get_memory_source(
            int(memory_id)
        ):
            repaired_metadata = dict(metadata)
            repaired_metadata["has_source"] = False
            repaired_metadata["source_message_count"] = 0
            if (
                self.hybrid_retriever is None
                or not await self.hybrid_retriever.update_metadata(
                    int(memory_id), repaired_metadata
                )
            ):
                raise RuntimeError("source metadata repair failed")
            metadata = repaired_metadata
            await self._advance_write_op(
                op_id,
                "source_metadata_repaired",
                memory_id=int(memory_id),
                error="retained source was unavailable after interrupted write",
            )
        content = str(memory.get("text") or "")
        session_id = metadata.get("session_id") or payload.get("session_id")
        persona_id = metadata.get("persona_id") or payload.get("persona_id")

        atom_payloads = payload.get("failed_atoms") or payload.get("atoms", []) or []
        atoms: list[MemoryAtom] = []
        for atom_payload in atom_payloads:
            if isinstance(atom_payload, dict):
                atom = self._deserialize_atom_from_repair(
                    atom_payload,
                    int(memory_id),
                    session_id,
                    persona_id,
                )
                if atom is not None:
                    atoms.append(atom)

        if self.atom_store is not None and atoms and self.atom_enabled:
            existing_atoms = await self.atom_store.get_by_parent(int(memory_id))
            if payload.get("failed_atoms"):
                existing_keys = {
                    (
                        atom.content,
                        atom.atom_type.value,
                        atom.session_id,
                        atom.persona_id,
                    )
                    for atom in existing_atoms
                }
                atoms_to_insert = [
                    atom
                    for atom in atoms
                    if (
                        atom.content,
                        atom.atom_type.value,
                        atom.session_id,
                        atom.persona_id,
                    )
                    not in existing_keys
                ]
                if atoms_to_insert:
                    await self.atom_store.insert_many(atoms_to_insert)
            elif not existing_atoms:
                await self.atom_store.insert_many(atoms)
            await self._advance_write_op(op_id, "atoms_repaired", memory_id=memory_id)

        if self.graph_memory_manager is not None and content.strip():
            await self.graph_memory_manager.index_memory(
                int(memory_id),
                content,
                metadata,
                atoms or None,
            )
            await self._advance_write_op(op_id, "graph_repaired", memory_id=memory_id)

        await self._advance_write_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def _repair_delete_write_op(
        self,
        op_id: int,
        memory_id: int | None,
    ) -> bool:
        if memory_id is None:
            await self._advance_write_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_id for delete repair",
            )
            return False

        if self.db_connection is not None:
            # Replay the document/vector/BM25 delete first, mirroring the normal
            # delete flow. If the process crashed before or during that step, the
            # document must be removed here too; otherwise the memory stays
            # searchable while its derived graph/atom/source data is already gone.
            cursor = await self.db_connection.execute(
                "SELECT 1 FROM documents WHERE id = ?", (int(memory_id),)
            )
            if await cursor.fetchone() is not None and self.hybrid_retriever is not None:
                await self.hybrid_retriever.delete_memory(int(memory_id))

            await self.db_connection.execute(
                "DELETE FROM memory_sources WHERE memory_id = ?", (int(memory_id),)
            )
            await self.db_connection.commit()
        if self.graph_memory_manager is not None:
            await self.graph_memory_manager.delete_memory(int(memory_id))
        if self.atom_store is not None:
            await self.atom_store.delete_by_parent(int(memory_id))

        await self._advance_write_op(
            op_id,
            "completed",
            status="completed",
            memory_id=int(memory_id),
        )
        return True

    async def _repair_batch_delete_write_op(
        self,
        op_id: int,
        payload: dict[str, Any],
    ) -> bool:
        memory_ids_raw = payload.get("memory_ids") or []
        if not isinstance(memory_ids_raw, list):
            await self._advance_write_op(
                op_id,
                "unrepairable",
                status="failed",
                error="missing memory_ids for batch delete repair",
            )
            return False

        memory_ids: list[int] = []
        for raw_id in memory_ids_raw:
            try:
                memory_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        if not memory_ids:
            await self._advance_write_op(
                op_id,
                "unrepairable",
                status="failed",
                error="empty memory_ids for batch delete repair",
            )
            return False

        await self._delete_document_indexes_for_batch(memory_ids)
        await self._delete_graph_and_atoms_for_batch(memory_ids)
        await self._advance_write_op(
            op_id,
            "completed",
            status="completed",
            payload_patch={"deleted_count": len(memory_ids)},
        )
        return True

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
            await self.graph_memory_manager.batch_delete_memories(memory_ids)
        if self.atom_store is not None:
            await self.atom_store.batch_delete_by_parent(memory_ids)

    @staticmethod
    def _safe_json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

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
