"""检索召回、缓存与读取接口。"""

import asyncio
import copy
import json
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ..memory_scope import (
    adapter_cache_key,
    adapter_sql,
    adapter_visible,
    current_adapter_access,
)
from ..memory_transfer import memory_import_key
from ..retrieval.hybrid_retriever import HybridResult
from ..retrieval.route_execution import search_route
from ..retrieval.vector_search import retrieve_documents
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float
from .agentic_recall_types import AgenticRecallOutcome as AgenticRecallOutcome


class MemoryEngineRecallMixin:
    """MemoryEngine 的 MemoryEngineRecallMixin 分组。

    宿主共享状态由 ``MemoryEngine`` 独占持有，本模块只通过 self 访问。
    """

    # 宿主共享状态契约（由 MemoryEngine.__init__ / initialize 赋值）
    db_connection: aiosqlite.Connection | None
    config: dict[str, Any]
    db_path: str
    faiss_db: Any
    atom_enabled: bool
    atom_store: AtomStore | None
    atom_retriever: Any | None
    vector_retriever: Any | None
    bm25_retriever: Any | None
    hybrid_retriever: Any | None
    index_writer: Any | None
    dual_route_retriever: Any | None
    graph_memory_manager: Any | None
    graph_store: Any | None
    _search_cache_enabled: bool
    _search_cache_ttl: float
    _search_cache_max_size: int
    _search_cache_generation: int
    _search_cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]]
    _access_update_pending: set[int]
    _access_atom_update_pending: set[int]
    _access_flush_task: asyncio.Task | None
    index_maintenance_status: dict[str, Any]

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[HybridResult]:
        """
        检索相关记忆

        Args:
            query: 查询字符串
            k: 返回数量
            session_id: 会话ID过滤(可选,应传入unified_msg_origin完整格式)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[HybridResult]: 检索结果列表
        """
        if not query or not query.strip():
            return []

        cache_key = self._search_cache_key(query, k, session_id, persona_id)
        cached_results = self._get_cached_search_results(cache_key)
        if cached_results is not None:
            cached_results = await self._apply_atom_policy(cached_results)
            await self._update_access_times_internal(
                [result.doc_id for result in cached_results],
                [
                    atom_id
                    for result in cached_results
                    for atom_id in result.metadata.get("retrieved_atom_ids", [])
                ],
            )
            return cached_results

        # 如果session_id是unified_msg_origin格式，自动触发旧数据迁移
        if (
            session_id
            and ":" in session_id
            and not session_id.startswith("livingmemory:")
        ):
            # 异步触发迁移，不阻塞查询
            self._create_tracked_task(self._migrate_session_data_if_needed(session_id))

        # 【关键修改】不再提取UUID，直接使用完整的unified_msg_origin进行匹配
        # 因为现在数据库中存储的就是完整格式
        # session_id 和 persona_id 保持原样传递给检索器

        # 执行混合检索 / 双路检索
        if self.dual_route_retriever is not None:
            results = await self.dual_route_retriever.search(
                query,
                k,
                session_id,
                persona_id,
            )
        else:
            if self.hybrid_retriever is None:
                raise RuntimeError("混合检索器未初始化")
            results = await self.hybrid_retriever.search(
                query, k, session_id, persona_id
            )

        if self.atom_retriever is not None:
            atom_results, _ = await search_route(
                "atoms",
                self.atom_retriever.search(query, k * 2, session_id, persona_id),
            )
            existing_ids = {result.doc_id for result in results}
            scores = {}
            for atom in atom_results:
                if atom.parent_memory_id not in existing_ids:
                    scores[atom.parent_memory_id] = max(
                        scores.get(atom.parent_memory_id, 0), atom.final_score
                    )
            if scores:
                documents = await retrieve_documents(
                    self.faiss_db,
                    metadata_filters={},
                    ids=list(scores),
                    limit=len(scores),
                )
                for document in documents:
                    metadata = safe_json_dict(document.get("metadata"))
                    if (
                        session_id is not None
                        and metadata.get("session_id") != session_id
                    ):
                        continue
                    if (
                        persona_id is not None
                        and metadata.get("persona_id") != persona_id
                    ):
                        continue
                    score = scores[int(document["id"])]
                    results.append(
                        HybridResult(
                            doc_id=int(document["id"]),
                            final_score=score,
                            rrf_score=0,
                            bm25_score=None,
                            vector_score=None,
                            content=document["text"],
                            metadata=metadata,
                            score_breakdown={"atom_score": score},
                        )
                    )
                results.sort(key=lambda result: result.final_score, reverse=True)

        results = await self._apply_atom_policy(results)
        results = self._filter_by_retrieval_policy(results)
        resolved_ids = {result.doc_id for result in results}
        results = await self._merge_recent_memories(
            results,
            k,
            session_id,
            persona_id,
        )
        # 只对合并进来的新候选做原子策略解析，避免重复查询已解析的文档
        results = await self._apply_atom_policy(results, skip_doc_ids=resolved_ids)

        # 访问统计合并到窗口内异步写，不阻塞返回
        if results:
            await self._update_access_times_internal(
                [r.doc_id for r in results],
                [
                    atom_id
                    for result in results
                    for atom_id in result.metadata.get("retrieved_atom_ids", [])
                ],
            )

        self._set_cached_search_results(cache_key, results)
        return results

    async def _apply_atom_policy(self, results, skip_doc_ids: set[int] | None = None):
        """Resolve atom-backed recall content from current lifecycle state.

        与 Beta 自主召回共用 `_agentic_atom_rows` / `_agentic_live_rows`：
        原子表的读取和 active/expires 过滤只有一份实现。

        Args:
            results: Document candidates, including cached or recent results.
            skip_doc_ids: Document ids already resolved in this recall; they are
                appended unchanged so the second merge pass does not re-query atoms.

        Returns:
            Candidates containing only live facts; fully expired parents are omitted.
        """
        if not results or not self.atom_enabled or self.atom_store is None:
            return results
        skip = skip_doc_ids or set()
        pending = [result for result in results if result.doc_id not in skip]
        grouped = await self._agentic_atom_rows([result.doc_id for result in pending])
        filtered = []
        for result in results:
            if result.doc_id in skip:
                filtered.append(result)
                continue
            atoms = grouped.get(result.doc_id)
            if atoms is None and not result.metadata.get("atom_types"):
                filtered.append(result)
                continue
            active = self._agentic_live_rows(atoms or [])
            if not active:
                continue
            filtered.append(
                replace(
                    result,
                    content="\n".join(dict.fromkeys(str(row[2]) for row in active)),
                    metadata={
                        **result.metadata,
                        "retrieved_atom_ids": [int(row[0]) for row in active],
                    },
                )
            )
        return filtered

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """
        根据ID获取记忆

        Args:
            memory_id: 记忆ID

        Returns:
            Optional[Dict]: 记忆数据,包含text和metadata
        """
        docs = await retrieve_documents(
            self.faiss_db, metadata_filters={}, ids=[memory_id], limit=1
        )
        if not docs:
            return None
        doc = docs[0]
        return {
            "id": doc["id"],
            "text": doc["text"],
            "metadata": doc["metadata"],
        }

    async def get_memory_record(self, memory_id: int) -> dict[str, Any] | None:
        """
        读取记忆的原始文档记录（供管理页面展示/编辑前快照使用）。

        与 get_memory() 不同，本方法直接读取 documents 表，
        返回 doc_id/created_at/updated_at 等原始列，metadata 保持原始字符串。

        Args:
            memory_id: 记忆ID(documents.id)

        Returns:
            Optional[Dict]: 原始记录字典，不存在时返回 None
        """
        if self.db_connection is None:
            logger.warning("[MemoryEngine] 数据库连接未初始化，无法读取记忆记录")
            return None
        try:
            cursor = await self.db_connection.execute(
                """
                SELECT id, doc_id, text, metadata, created_at, updated_at
                FROM documents
                WHERE id = ?
                """,
                (memory_id,),
            )
            row = await cursor.fetchone()
            if not row or not adapter_visible(safe_json_dict(row["metadata"])):
                return None
            return {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "text": row["text"],
                "metadata": row["metadata"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[MemoryEngine] 读取记忆记录失败", exc_info=True)
            return None

    async def list_memories_page(
        self,
        *,
        session_id: str | None = None,
        keyword: str = "",
        status: str = "all",
        memory_type: str | None = None,
        sort_key: str = "created_desc",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        分页列出记忆（供管理页面使用），支持会话/状态/类型/关键词过滤与排序。

        过滤/排序表达式必须与 documents 上的表达式索引逐字匹配
        （idx_doc_memory_type / idx_doc_status / idx_doc_create_time），
        不得用 CASE WHEN json_valid 包装，否则索引失效退化为全表扫描。

        Args:
            session_id: 会话ID过滤
            keyword: 关键词过滤（纯数字时同时匹配记忆ID）
            status: 状态过滤（all/active/archived）
            memory_type: 记忆类型过滤（GENERAL/FACT/...）
            sort_key: 排序键，不在白名单内时回退为 created_desc
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            Dict: 包含 items/total/page/page_size/has_more/sort 的字典

        Raises:
            RuntimeError: 数据库连接未初始化时抛出
        """
        if self.db_connection is None:
            raise RuntimeError("数据库连接未初始化")

        offset = (max(1, page) - 1) * page_size
        ownership, ownership_params = adapter_sql("metadata")
        where_clauses: list[str] = [ownership]
        params: list[Any] = list(ownership_params)
        type_expr = (
            "UPPER(COALESCE(json_extract(metadata, '$.memory_type'), 'GENERAL'))"
        )

        if session_id:
            where_clauses.append("json_extract(metadata, '$.session_id') = ?")
            params.append(session_id)

        if status and status != "all":
            where_clauses.append(
                "COALESCE(json_extract(metadata, '$.status'), 'active') = ?"
            )
            params.append(status)

        if memory_type:
            where_clauses.append(f"{type_expr} = ?")
            params.append(memory_type.upper())

        if keyword:
            keyword_like = f"%{keyword}%"
            if keyword.isdigit():
                where_clauses.append(
                    "(CAST(id AS TEXT) = ? OR text LIKE ? COLLATE NOCASE)"
                )
                params.extend([keyword, keyword_like])
            else:
                where_clauses.append(
                    "("
                    "text LIKE ? COLLATE NOCASE "
                    "OR COALESCE(json_extract(metadata, '$.memory_type'), '') LIKE ? COLLATE NOCASE"
                    ")"
                )
                params.extend([keyword_like, keyword_like])

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        # 与 idx_doc_create_time 首列逐字匹配，created_desc/asc 可走索引排序
        created_expr = (
            "COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0)"
        )
        updated_expr = (
            "COALESCE("
            "CAST(json_extract(metadata, '$.updated_at') AS REAL),"
            "COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0),"
            "0)"
        )
        importance_raw_expr = (
            "COALESCE(CAST(json_extract(metadata, '$.importance') AS REAL), 0.5)"
        )
        importance_expr = (
            f"CASE WHEN {importance_raw_expr} <= 1.0 "
            f"THEN {importance_raw_expr} * 10.0 ELSE {importance_raw_expr} END"
        )
        sort_options = {
            "created_desc": f"{created_expr} DESC, id DESC",
            "created_asc": f"{created_expr} ASC, id ASC",
            "updated_desc": f"{updated_expr} DESC, id DESC",
            "updated_asc": f"{updated_expr} ASC, id ASC",
            "importance_desc": f"{importance_expr} DESC, id DESC",
            "importance_asc": f"{importance_expr} ASC, id ASC",
            "type_asc": f"{type_expr} ASC, id DESC",
            "type_desc": f"{type_expr} DESC, id DESC",
            "id_desc": "id DESC",
            "id_asc": "id ASC",
        }
        sort_expr = sort_options.get(sort_key)
        if sort_expr is None:
            sort_key = "created_desc"
            sort_expr = sort_options[sort_key]

        count_cursor = await self.db_connection.execute(
            f"SELECT COUNT(*) AS total FROM documents {where_clause}",
            params,
        )
        count_row = await count_cursor.fetchone()
        total = int(count_row["total"]) if count_row else 0

        cursor = await self.db_connection.execute(
            f"""
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents
            {where_clause}
            ORDER BY {sort_expr}
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        )
        rows = await cursor.fetchall()

        items = [
            {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "text": row["text"],
                "metadata": row["metadata"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": (offset + page_size) < total,
            "sort": sort_key,
        }

    async def find_similar_pairs(
        self, memory_ids: list[int], threshold: float
    ) -> list[tuple[int, int, float]]:
        """
        批量查找语义相似的记忆对（供记忆整合的语义聚类使用）。

        Args:
            memory_ids: 候选记忆ID列表
            threshold: 相似度阈值

        Returns:
            List[Tuple[int, int, float]]: (id_a, id_b, similarity) 相似对列表

        Raises:
            RuntimeError: 向量检索器未就绪时抛出
        """
        if self.vector_retriever is None:
            raise RuntimeError("vector_retriever 未就绪")
        return await self.vector_retriever.find_similar_pairs(memory_ids, threshold)

    async def find_similar_memory(
        self,
        content: str,
        *,
        session_id: str | None = None,
        persona_id: str | None = None,
        threshold: float = 0.92,
        limit: int = 3,
    ) -> dict[str, Any] | None:
        """Return the most similar active memory in the same scope, if any.

        Args:
            content: Newly generated summary text.
            session_id: Memory scope to compare within.
            persona_id: Persona filter used by the vector retriever.
            threshold: Minimum cosine similarity to treat as a duplicate.
            limit: Number of candidates fetched from the vector index.

        Returns:
            The best matching ``{"id", "similarity"}`` above the threshold, else None.
        """
        if not content or not content.strip() or self.vector_retriever is None:
            return None
        try:
            results = await self.vector_retriever.search(
                content,
                max(1, min(int(limit), 20)),
                session_id=session_id,
                persona_id=persona_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[MemoryEngine] 近重复检测失败，按新记忆写入", exc_info=True)
            return None

        best: dict[str, Any] | None = None
        for result in results or []:
            score = float(getattr(result, "score", 0.0) or 0.0)
            if score < threshold:
                continue
            doc_id = int(getattr(result, "doc_id", 0) or 0)
            if not doc_id:
                continue
            if best is None or score > best["similarity"]:
                best = {"id": doc_id, "similarity": score}
        return best

    async def get_memory_source(self, memory_id: int) -> list[dict[str, Any]]:
        """Return structured source messages for one memory."""
        if (
            current_adapter_access.get() is not None
            and await self.get_memory(memory_id) is None
        ):
            return []
        if self.db_connection is None:
            return []
        cursor = await self.db_connection.execute(
            "SELECT source_json FROM memory_sources WHERE memory_id = ?",
            (int(memory_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return []
        try:
            value = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return []
        return value if isinstance(value, list) else []

    async def get_memory_transfer_records(
        self, memory_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Return portable memory records without retrieval-index internals."""
        if self.db_connection is None:
            return []

        normalized_ids: list[int] | None = None
        if memory_ids is not None:
            normalized_ids = list(dict.fromkeys(int(item) for item in memory_ids))
            if not normalized_ids:
                return []

        async def _fetch_rows(batch: list[int] | None):
            params: list[Any] = []
            ownership, ownership_params = adapter_sql("d.metadata")
            where_clause = f"WHERE {ownership}"
            params.extend(ownership_params)
            if batch is not None:
                placeholders = ",".join("?" * len(batch))
                where_clause += f" AND d.id IN ({placeholders})"
                params.extend(batch)
            cursor = await self.db_connection.execute(
                f"""
            SELECT d.id, d.text, d.metadata, d.created_at, d.updated_at,
                   s.source_json
            FROM documents AS d
            LEFT JOIN memory_sources AS s ON s.memory_id = d.id
            {where_clause}
            ORDER BY d.id ASC
            """,
                params,
            )
            return await cursor.fetchall()

        if normalized_ids is None:
            rows = await _fetch_rows(None)
        else:
            rows = []
            for offset in range(0, len(normalized_ids), 500):
                rows.extend(await _fetch_rows(normalized_ids[offset : offset + 500]))
            rows.sort(key=lambda row: int(row["id"]))
        records: list[dict[str, Any]] = []

        def _build_records() -> list[dict[str, Any]]:
            # 逐行 JSON 解析属于 CPU 密集工作，整体在 to_thread 中执行
            for row in rows:
                metadata = safe_json_dict(row["metadata"])
                source_messages: list[dict[str, Any]] = []
                if row["source_json"]:
                    try:
                        parsed_source = json.loads(row["source_json"])
                    except (json.JSONDecodeError, TypeError):
                        parsed_source = []
                    if isinstance(parsed_source, list):
                        source_messages = parsed_source
                records.append(
                    {
                        "original_id": int(row["id"]),
                        "content": str(row["text"] or ""),
                        "importance": clamp_float(
                            metadata.get("importance"), default=0.5
                        ),
                        "session_id": metadata.get("session_id"),
                        "persona_id": metadata.get("persona_id"),
                        "metadata": metadata,
                        "source_messages": source_messages,
                        "storage_created_at": row["created_at"],
                        "storage_updated_at": row["updated_at"],
                    }
                )
            return records

        await asyncio.to_thread(_build_records)
        return records

    async def get_memory_import_keys(self) -> set[tuple[str, str, str]]:
        """Return duplicate keys for existing memories."""
        if self.db_connection is None:
            return set()
        # 只取去重键所需的列，避免把整份 metadata 拉进内存
        cursor = await self.db_connection.execute(
            "SELECT text, "
            "CASE WHEN json_valid(metadata) "
            "THEN json_extract(metadata, '$.session_id') END AS session_id, "
            "CASE WHEN json_valid(metadata) "
            "THEN json_extract(metadata, '$.persona_id') END AS persona_id "
            "FROM documents"
        )
        rows = await cursor.fetchall()

        def _compute_keys() -> set[tuple[str, str, str]]:
            return {
                memory_import_key(
                    str(row["text"] or ""),
                    row["session_id"],
                    row["persona_id"],
                )
                for row in rows
            }

        return await asyncio.to_thread(_compute_keys)

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
            adapter_cache_key(),
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
            int(self.config.get("recent_memory_count", 0)),
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
        """按创建时间倒序取当前范围内的近期记忆，作为相关性不足时的补位候选。

        结果固定 final_score=0.6 并标记 score_breakdown["recent_memory"]，
        使其排序始终低于正常相关性结果，只在相关性结果不足时被补入。

        Args:
            count: 最多返回的近期记忆条数。
            session_id: 会话过滤条件，None 表示不限制。
            persona_id: 人格过滤条件，None 表示不限制。

        Returns:
            最多 count 条近期记忆结果，按时间从新到旧排列。
        """
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
        ownership, ownership_params = adapter_sql("metadata")
        conditions.append(ownership)
        params.extend(ownership_params)
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
                final_score=0.6,
                rrf_score=0.0,
                bm25_score=None,
                vector_score=None,
                content=str(row["text"] or ""),
                metadata=safe_json_dict(row["metadata"]),
                score_breakdown={"recent_memory": 0.6},
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
        """仅在相关性结果不足 k 条时用近期记忆补位。

        近期记忆不预留固定槽位：相关性结果已足够时直接返回，避免无关的
        最新记忆挤占召回结果；不足时才按时间顺序补入，且跳过已存在的 doc_id。

        Args:
            results: 已过滤的相关性结果，按得分降序排列。
            k: 单次召回的目标数量。
            session_id: 会话过滤条件，None 表示不限制。
            persona_id: 人格过滤条件，None 表示不限制。

        Returns:
            最多 k 条结果：优先保留相关性结果，不足时补入近期记忆。
        """
        recent_count = min(max(0, int(self.config.get("recent_memory_count", 0))), k)
        if recent_count <= 0 or len(results) >= k:
            return results[:k]

        recent = await self._get_recent_memory_results(
            recent_count, session_id, persona_id
        )
        selected = list(results)
        selected_ids = {result.doc_id for result in selected}
        for result in recent:
            if len(selected) >= k:
                break
            if result.doc_id in selected_ids:
                continue
            selected.append(result)
            selected_ids.add(result.doc_id)
        return selected

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
