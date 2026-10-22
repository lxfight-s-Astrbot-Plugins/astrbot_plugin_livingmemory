"""
MemoryEngine 的 MemoryEngineCrudMixin 拆分模块
自动从 core/managers/memory_engine.py 拆分，保持行为不变
"""

import asyncio
import json
import time
from dataclasses import replace
from typing import Any

from astrbot.api import logger

from ..memory_transfer import memory_import_key
from ..processors.atom_classifier import classify_atoms
from ..retrieval.hybrid_retriever import HybridResult
from ..retrieval.route_execution import search_route
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float, safe_float
from ...storage.atom_store import AtomStore

import aiosqlite

from .agentic_recall_types import AgenticRecallOutcome as AgenticRecallOutcome


class MemoryEngineCrudMixin:
    """MemoryEngine 拆分模块：MemoryEngineCrudMixin

    下述类级注解声明本模块依赖的宿主共享状态（由 ``MemoryEngine.__init__`` /
    ``initialize`` 赋值，见 core/managers/memory_engine.py）。仅作类型约束，
    不在此赋默认值——宿主是共享状态的唯一属主。
    """

    # 访问统计合并窗口：窗口内的多次召回合并为一次 UPDATE
    _ACCESS_UPDATE_DEBOUNCE_SECONDS = 3.0

    # 宿主共享状态契约
    db_connection: aiosqlite.Connection | None
    config: dict[str, Any]
    faiss_db: Any
    atom_enabled: bool
    atom_store: AtomStore | None
    atom_retriever: Any | None
    vector_retriever: Any | None
    hybrid_retriever: Any | None
    index_writer: Any | None
    dual_route_retriever: Any | None
    graph_memory_manager: Any | None
    _access_update_pending: set[int]
    _access_atom_update_pending: set[int]
    _access_flush_task: asyncio.Task | None

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        preserve_create_time: bool = False,
        source_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        """添加新记忆。

        Args:
            content: 记忆内容。
            session_id: 会话 ID（保存完整 unified_msg_origin）。
            persona_id: 人格 ID。
            importance: 重要性（0-1）。
            metadata: 额外元数据。
            atoms: 记忆原子列表。
            preserve_create_time: 物理替换时保留原记忆的时间轴位置。
            source_messages: 原文消息。

        Returns:
            int: 记忆 ID（doc_id）。

        Raises:
            ValueError: 内容为空。
            RuntimeError: 索引写入器未初始化。
        """
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")
        if self.index_writer is None:
            raise RuntimeError("索引写入器未初始化")

        full_metadata = self._build_memory_metadata(
            session_id=session_id,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
            atoms=atoms,
            preserve_create_time=preserve_create_time,
            source_messages=source_messages,
        )
        doc_id = await self.index_writer.add_memory(content, full_metadata)
        await self._write_memory_atoms(doc_id, session_id, persona_id, atoms)
        await self._index_memory_graph(doc_id, content, full_metadata, atoms)
        await self._persist_memory_source(doc_id, source_messages)
        self._invalidate_search_cache()
        return doc_id

    def _build_memory_metadata(
        self,
        *,
        session_id: str | None,
        persona_id: str | None,
        importance: float,
        metadata: dict[str, Any] | None,
        atoms: list | None,
        preserve_create_time: bool,
        source_messages: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """合并调用方元数据与时间/原子类型字段，返回待写入的完整元数据。"""
        current_time = time.time()
        full_metadata: dict[str, Any] = {
            "session_id": session_id,  # 保存完整的 unified_msg_origin
            "persona_id": persona_id,  # 保存完整的 persona_id
            "importance": max(0.0, min(1.0, importance)),  # 限制在0-1范围
            "create_time": current_time,
            "last_access_time": current_time,
            "status": "active",
        }

        # 先合并外部 metadata，再确保时间字段不被覆盖
        if metadata:
            full_metadata.update(metadata)
        if source_messages:
            full_metadata["has_source"] = True
            full_metadata["source_message_count"] = len(source_messages)
        if atoms:
            full_metadata["atom_types"] = sorted(
                {
                    getattr(getattr(atom, "atom_type", None), "value", "unknown")
                    for atom in atoms
                }
            )

        # 普通新增使用当前时间；物理替换保留原记忆的时间轴位置。
        preserved_create_time = None
        if preserve_create_time and metadata:
            try:
                preserved_create_time = float(metadata.get("create_time"))
            except (TypeError, ValueError):
                preserved_create_time = None
        full_metadata["create_time"] = (
            preserved_create_time if preserved_create_time is not None else current_time
        )
        full_metadata["last_access_time"] = current_time
        return full_metadata

    async def _write_memory_atoms(
        self,
        doc_id: int,
        session_id: str | None,
        persona_id: str | None,
        atoms: list | None,
    ) -> None:
        """写入记忆原子：批量失败时逐条重试，仍失败的留给启动时对账。"""
        if not atoms or self.atom_store is None or not self.atom_enabled:
            return
        prepared_atoms = []
        for atom in atoms:
            atom.session_id = atom.session_id or session_id
            atom.persona_id = atom.persona_id or persona_id
            atom.parent_memory_id = doc_id
            prepared_atoms.append(atom)
        try:
            await self.atom_store.insert_many(prepared_atoms)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[MemoryEngine] 批量写入记忆原子失败，改为逐条重试",
                exc_info=True,
            )
            for atom in prepared_atoms:
                if getattr(atom, "atom_id", 0):
                    continue
                try:
                    await self.atom_store.insert(atom)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error(
                        f"[MemoryEngine] 写入记忆原子失败: {atom.content[:80]}",
                        exc_info=True,
                    )

    async def _index_memory_graph(
        self,
        doc_id: int,
        content: str,
        full_metadata: dict[str, Any],
        atoms: list | None,
    ) -> None:
        """建立图记忆索引；失败只记录日志，由重建对账。"""
        if self.graph_memory_manager is None:
            return
        try:
            indexed = await self.graph_memory_manager.index_memory(
                doc_id, content, full_metadata, atoms
            )
            if indexed is False:
                raise RuntimeError("Graph indexing deferred until rebuild completes")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 图记忆索引失败 (memory_id={doc_id})",
                exc_info=True,
            )

    async def _persist_memory_source(
        self,
        doc_id: int,
        source_messages: list[dict[str, Any]] | None,
    ) -> None:
        """保存原文消息；失败时回滚刚写入的记忆。"""
        if not source_messages:
            return
        try:
            await self.save_memory_source(doc_id, source_messages)
        except asyncio.CancelledError:
            await asyncio.shield(self.delete_memory(doc_id))
            raise
        except Exception:
            if not await self.delete_memory(doc_id):
                logger.error(
                    f"[MemoryEngine] 原文写入失败且记忆回滚失败 (memory_id={doc_id})"
                )
            raise

    async def save_memory_source(
        self, memory_id: int, source_messages: list[dict[str, Any]]
    ) -> None:
        """Persist source messages outside retrieval metadata and indexes."""
        if self.db_connection is None:
            raise RuntimeError("数据库连接未初始化")
        now = time.time()
        await self.db_connection.execute(
            """
            INSERT INTO memory_sources(memory_id, source_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                source_json = excluded.source_json,
                updated_at = excluded.updated_at
            """,
            (
                int(memory_id),
                json.dumps(source_messages, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self.db_connection.commit()

    async def get_memory_source(self, memory_id: int) -> list[dict[str, Any]]:
        """Return structured source messages for one memory."""
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
            where_clause = ""
            if batch is not None:
                placeholders = ",".join("?" * len(batch))
                where_clause = f"WHERE d.id IN ({placeholders})"
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
                documents = await self.faiss_db.document_storage.get_documents(
                    metadata_filters={}, ids=list(scores), limit=len(scores)
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
        grouped = await self._agentic_atom_rows(
            [result.doc_id for result in pending]
        )
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
                    content="\n".join(
                        dict.fromkeys(str(row[2]) for row in active)
                    ),
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
        # 从faiss_db的document_storage获取文档
        try:
            # 使用 get_documents (复数) 并传入 ids 参数
            docs = await self.faiss_db.document_storage.get_documents(
                metadata_filters={}, ids=[memory_id], limit=1
            )

            if not docs or len(docs) == 0:
                return None

            doc = docs[0]
            return {
                "id": doc["id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[MemoryEngine] 获取记忆详情失败", exc_info=True)
            return None

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
            if not row:
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
        where_clauses: list[str] = []
        params: list[Any] = []
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

    async def merge_duplicate_memory(
        self,
        memory_id: int,
        *,
        importance: float,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        source_messages: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Merge a new summary into an existing near-duplicate memory.

        The existing summary text is kept (avoiding another embedding pass);
        importance, topics, key facts, source windows, sources and new atoms are
        folded in so the new facts remain retrievable.

        Args:
            memory_id: Existing memory document id.
            importance: Importance of the incoming summary.
            metadata: Incoming summary metadata.
            atoms: Newly classified memory atoms.
            source_messages: Optional retained source messages.

        Returns:
            True when the merge was persisted.
        """
        if self.db_connection is None:
            return False
        memory = await self.get_memory(memory_id)
        if not memory:
            return False
        metadata = metadata or {}
        current_metadata = safe_json_dict(memory.get("metadata"))
        patch: dict[str, Any] = {}

        for key in ("topics", "key_facts", "participants", "participant_identities"):
            incoming = metadata.get(key)
            if not incoming:
                continue
            merged = self._merge_unique_values(current_metadata.get(key), incoming)
            if merged:
                patch[key] = merged

        now = time.time()
        source_window = metadata.get("source_window")
        if isinstance(source_window, dict):
            windows = list(current_metadata.get("source_windows") or [])
            windows.append(source_window)
            patch["source_windows"] = windows[-20:]

        patch["last_access_time"] = now
        patch["dedup_merge_count"] = (
            int(current_metadata.get("dedup_merge_count") or 0) + 1
        )
        patch["dedup_last_merged_at"] = now

        current_importance = clamp_float(current_metadata.get("importance"), default=0.5)
        merged_importance = max(current_importance, clamp_float(importance, default=0.5))
        try:
            success = await self.update_memory(
                memory_id,
                {"importance": merged_importance, "metadata": patch},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                f"[MemoryEngine] 合并近重复记忆失败 (memory_id={memory_id})",
                exc_info=True,
            )
            return False
        if not success:
            return False

        if atoms and self.atom_store is not None and self.atom_enabled:
            await self._append_atoms_to_memory(memory_id, atoms, metadata)

        dedup_cfg = self.config.get("memory_dedup") or {}
        if source_messages and bool(dedup_cfg.get("merge_sources", True)):
            max_sources = int(dedup_cfg.get("max_source_messages", 50) or 50)
            existing_sources = await self.get_memory_source(memory_id)
            combined: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in [*existing_sources, *source_messages]:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                combined.append(item)
            try:
                await self.save_memory_source(memory_id, combined[-max_sources:])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[MemoryEngine] 合并来源消息失败 (memory_id={memory_id})",
                    exc_info=True,
                )

        logger.info(
            f"[MemoryEngine] 已合并近重复记忆到 #{memory_id}"
            f"（相似度合并次数={patch['dedup_merge_count']}）"
        )
        return True

    @staticmethod
    def _merge_unique_values(existing: Any, incoming: Any) -> list[Any]:
        """Union two metadata lists without reordering existing entries."""
        merged: list[Any] = []
        seen: set[str] = set()
        for item in [*(existing or []), *(incoming or [])]:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    async def _append_atoms_to_memory(
        self,
        memory_id: int,
        atoms: list,
        metadata: dict[str, Any],
    ) -> None:
        """Attach newly classified atoms to an existing memory, skipping duplicates."""
        if self.db_connection is None or self.atom_store is None:
            return
        cursor = await self.db_connection.execute(
            "SELECT content FROM memory_atoms WHERE parent_memory_id = ?",
            (int(memory_id),),
        )
        existing_contents = {
            str(row[0]).strip() for row in await cursor.fetchall() if row[0]
        }
        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        for atom in atoms:
            content = str(getattr(atom, "content", "") or "").strip()
            if not content or content in existing_contents:
                continue
            atom.parent_memory_id = memory_id
            atom.session_id = atom.session_id or session_id
            atom.persona_id = atom.persona_id or persona_id
            try:
                await self.atom_store.insert(atom)
                existing_contents.add(content)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[MemoryEngine] 合并记忆原子失败 (memory_id={memory_id}): {content[:60]}",
                    exc_info=True,
                )

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
    ) -> bool:
        """
        更新记忆（确保多数据库同步）

        支持更新内容、重要性、元数据等。采用不同策略：
        - 内容更新：先创建后删除（避免数据丢失）+ 全库同步
        - 元数据更新：三库同步更新

        Args:
            memory_id: 记忆ID
            updates: 更新字典,可包含:
                - content: 新内容 (触发完整重建)
                - importance: 新重要性
                - metadata: 元数据更新

        Returns:
            bool: 是否更新成功
        """
        # 获取当前记忆
        memory = await self.get_memory(memory_id)
        if not memory:
            logger.error(f"[更新] 记忆不存在 (memory_id={memory_id})")
            return False

        # 解析 metadata（可能是JSON字符串）
        current_metadata = safe_json_dict(memory.get("metadata", {}))

        # 处理内容更新 (需要重建所有索引)
        if "content" in updates:
            new_content = updates["content"]
            if not new_content or not new_content.strip():
                return False

            try:
                importance = clamp_float(
                    updates.get("importance", current_metadata.get("importance", 0.5)),
                    default=0.5,
                )

                # 构建新元数据
                new_metadata = current_metadata.copy()
                metadata_patch = updates.get("metadata")
                if isinstance(metadata_patch, dict):
                    new_metadata.update(metadata_patch)
                new_metadata["updated_at"] = time.time()
                new_metadata["previous_id"] = memory_id  # 记录旧ID
                new_metadata["importance"] = importance

                # 【改进】先创建新记忆，再删除旧记忆（避免数据丢失）
                logger.info(f"[更新] 开始内容更新流程 (old_id={memory_id})")

                new_memory_id = await self.replace_memory(
                    memory_id,
                    content=new_content,
                    importance=importance,
                    metadata=new_metadata,
                )

                logger.info(
                    f"[更新] 内容更新完成 (old_id={memory_id} → new_id={new_memory_id})"
                )
                self._invalidate_search_cache()
                return True

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"[更新] 内容更新失败 (memory_id={memory_id}): {e}", exc_info=True
                )
                return False

        # 处理非内容的元数据更新（不需要重建索引）
        metadata_updates = {}

        if "importance" in updates:
            metadata_updates["importance"] = clamp_float(
                updates["importance"], default=0.5
            )

        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])

        if metadata_updates:
            # 确保 current_metadata 是字典（再次检查）
            current_metadata = safe_json_dict(current_metadata)

            # 合并元数据
            current_metadata.update(metadata_updates)
            current_metadata["updated_at"] = time.time()

            # 使用写侧索引维护器确保多存储元数据同步
            if self.index_writer is None:
                logger.error("索引写入器未初始化")
                return False
            success = await self.index_writer.update_metadata(
                memory_id, metadata_updates
            )

            if success:
                logger.info(f"[更新] 元数据更新成功 (memory_id={memory_id})")
                # 仅当更新触及图谱提取依赖的字段时才重建图谱索引；
                # 状态/类型等纯元数据编辑重建整图（删节点+FAISS 落盘）纯属浪费
                graph_affecting = metadata_updates.keys() & {
                    "participants",
                    "participant_identities",
                    "topics",
                    "key_facts",
                    "canonical_summary",
                    "session_id",
                    "persona_id",
                    "importance",
                    "create_time",
                    "summary_schema_version",
                    "source_window",
                }
                if graph_affecting and self.graph_memory_manager is not None:
                    await self.graph_memory_manager.index_memory(
                        memory_id,
                        memory["text"],
                        current_metadata,
                    )
                self._invalidate_search_cache()
            else:
                logger.error(f"[更新] 元数据更新失败 (memory_id={memory_id})")

            return success

        return True

    async def replace_memory(
        self,
        memory_id: int,
        *,
        content: str,
        metadata: dict[str, Any],
        importance: float,
    ) -> int:
        """Replace one memory and rebuild all derived data with a new ID."""
        current = await self.get_memory(memory_id)
        if not current:
            raise ValueError(f"记忆不存在 (memory_id={memory_id})")
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")

        current_metadata = safe_json_dict(current.get("metadata"))
        source_messages = await self.get_memory_source(memory_id)
        replacement_metadata = current_metadata.copy()
        replacement_metadata.update(metadata or {})
        replacement_metadata["previous_id"] = memory_id
        replacement_metadata["updated_at"] = time.time()
        replacement_metadata["create_time"] = current_metadata.get("create_time")
        normalized_importance = clamp_float(importance, default=0.5)
        replacement_metadata["importance"] = normalized_importance

        session_id = replacement_metadata.get("session_id")
        persona_id = replacement_metadata.get("persona_id")
        raw_key_facts = replacement_metadata.get("key_facts")
        raw_topics = replacement_metadata.get("topics")
        raw_participants = replacement_metadata.get("participants")
        key_facts = raw_key_facts if isinstance(raw_key_facts, list) else []
        topics = raw_topics if isinstance(raw_topics, list) else []
        participants = raw_participants if isinstance(raw_participants, list) else []
        atoms = []
        if self.atom_enabled:
            atoms = classify_atoms(
                key_facts=key_facts,
                topics=topics,
                participants=participants,
                parent_importance=normalized_importance,
                session_id=session_id,
                persona_id=persona_id,
            )

        new_memory_id: int | None = None
        add_task = self._create_tracked_task(
            self.add_memory(
                content=content,
                session_id=session_id,
                persona_id=persona_id,
                importance=normalized_importance,
                metadata=replacement_metadata,
                atoms=atoms,
                preserve_create_time=True,
                source_messages=source_messages or None,
            )
        )
        try:
            new_memory_id = await asyncio.shield(add_task)
            if new_memory_id is None:
                raise RuntimeError("新记忆创建失败")
            if not await self.delete_memory(memory_id):
                await self.delete_memory(new_memory_id)
                new_memory_id = None
                raise RuntimeError("旧记忆删除失败，已回滚新记忆")
            self._invalidate_search_cache()
            return new_memory_id
        except asyncio.CancelledError:
            if new_memory_id is None:
                try:
                    new_memory_id = await asyncio.shield(add_task)
                except Exception:
                    new_memory_id = None
            if new_memory_id is not None:
                await asyncio.shield(self.delete_memory(new_memory_id))
            self._invalidate_search_cache()
            raise
        except Exception:
            if new_memory_id is not None:
                try:
                    if await self.get_memory(new_memory_id):
                        await self.delete_memory(new_memory_id)
                except Exception:
                    logger.error(
                        f"[更新] 回滚新记忆失败 (memory_id={new_memory_id})",
                        exc_info=True,
                    )
            self._invalidate_search_cache()
            raise

    async def delete_memory(self, memory_id: int) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否删除成功
        """

        # index_writer.delete_memory() 内部已按顺序删除 BM25、向量索引和 documents 表
        if self.index_writer is None:
            logger.error("索引写入器未初始化")
            return False
        success = await self.index_writer.delete_memory(memory_id)
        if not success:
            return False

        if self.db_connection is not None:
            await self.db_connection.execute(
                "DELETE FROM memory_sources WHERE memory_id = ?", (memory_id,)
            )
            await self.db_connection.commit()

        try:
            if self.graph_memory_manager is not None:
                deleted = await self.graph_memory_manager.delete_memory(memory_id)
                if deleted is False:
                    raise RuntimeError(
                        "Graph deletion deferred until rebuild completes"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 图记忆删除失败 (memory_id={memory_id})",
                exc_info=True,
            )

        try:
            if self.atom_store is not None:
                await self.atom_store.delete_by_parent(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 记忆原子删除失败 (memory_id={memory_id})",
                exc_info=True,
            )

        self._invalidate_search_cache()
        return success

    async def rebuild_graph_index(self) -> dict[str, int]:
        """Stream active documents into a safe graph-memory rebuild."""
        if self.graph_memory_manager is None:
            return {"rebuilt": 0, "skipped": 0}

        if self.db_connection is None:
            raise RuntimeError("memory database is not initialized")

        async def active_memory_batches():
            last_id = 0
            batch_size = 200
            while True:
                cursor = await self.db_connection.execute(
                    """
                    SELECT id, text, metadata
                    FROM documents
                    WHERE id > ?
                      AND COALESCE(
                          json_extract(metadata, '$.status'), 'active'
                      ) = 'active'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (last_id, batch_size),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break

                batch: list[tuple[int, str, dict[str, Any]]] = []
                for row in rows:
                    metadata = row["metadata"] or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                    elif not isinstance(metadata, dict):
                        metadata = {}
                    batch.append((int(row["id"]), str(row["text"] or ""), metadata))

                last_id = int(rows[-1]["id"])
                yield batch

        self._invalidate_search_cache()
        return await self.graph_memory_manager.rebuild_memory_batches(
            active_memory_batches()
        )

    async def apply_daily_decay(self, decay_rate: float, days: int = 1) -> int:
        """
        批量应用重要性衰减

        Args:
            decay_rate: 每日衰减率 (0-1)
            days: 衰减天数（用于补偿错过的天数）

        Returns:
            int: 受影响的记忆数量；执行失败返回 -1（调用方不应据此落盘“已衰减”状态）
        """
        if decay_rate <= 0 or days <= 0:
            return 0

        if self.db_connection is None:
            logger.error("[衰减] 数据库连接未初始化")
            return -1

        try:
            if decay_rate >= 1:
                decay_rate = 1.0
            access_window_days = float(
                self.config.get("access_decay_window_days", 30.0)
            )
            max_access_count = float(self.config.get("access_decay_max_count", 10.0))
            access_decay_multiplier = float(
                self.config.get("access_count_decay_multiplier", 0.5)
            )
            protected_threshold = clamp_float(
                self.config.get("protected_importance_threshold"), default=1.0
            )
            access_window_start = time.time() - max(1.0, access_window_days) * 86400.0
            access_decay_multiplier = max(0.0, min(1.0, access_decay_multiplier))
            cursor = await self.db_connection.execute(
                "SELECT id, metadata FROM documents WHERE json_extract(metadata, '$.importance') IS NOT NULL OR metadata LIKE '%\"importance\"%'"
            )
            rows = await cursor.fetchall()

            def _compute_updates() -> list[tuple[float, int]]:
                updates: list[tuple[float, int]] = []
                for row in rows:
                    metadata = safe_json_dict(row["metadata"])
                    importance = clamp_float(metadata.get("importance"), default=0.5)
                    if importance >= protected_threshold:
                        continue
                    access_count = safe_float(metadata.get("access_count"), 0.0)
                    last_access_time = safe_float(metadata.get("last_access_time"), 0.0)

                    recent_access_factor = (
                        1.0 if last_access_time >= access_window_start else 0.5
                    )
                    access_factor = min(1.0, access_count / max(1.0, max_access_count))
                    effective_decay_rate = decay_rate * (
                        1 - 0.5 * access_factor * recent_access_factor
                    )
                    decay_factor = (1 - effective_decay_rate) ** days
                    updates.append((decay_factor, int(row["id"])))
                return updates

            # 卸载逐行 JSON 解析与数值计算，避免阻塞事件循环。
            updates = await asyncio.to_thread(_compute_updates)

            if not updates:
                return 0

            # 只改 importance / access_count 两个键，且新值在 SQL 里基于当前值计算：
            # 并发召回写入的 last_access_time（以及期间新增的 access_count）不会被
            # 旧快照整列回写覆盖。
            await self.db_connection.executemany(
                """
                UPDATE documents
                SET metadata = json_set(
                    json_set(
                        metadata,
                        '$.importance',
                        MAX(
                            0.01,
                            ROUND(
                                CAST(json_extract(metadata, '$.importance') AS REAL) * ?,
                                4
                            )
                        )
                    ),
                    '$.access_count',
                    CAST(
                        COALESCE(
                            CAST(json_extract(metadata, '$.access_count') AS REAL),
                            0
                        ) * ?
                        AS INTEGER
                    )
                )
                WHERE id = ?
                """,
                [
                    (factor, access_decay_multiplier, doc_id)
                    for factor, doc_id in updates
                ],
            )

            await self.db_connection.commit()
            affected = len(updates)

            logger.info(
                f"[衰减] 批量衰减完成: 衰减率={decay_rate}, 天数={days}, "
                f"访问窗口={access_window_days:.1f}天, 影响记录={affected}"
            )

            self._invalidate_search_cache()
            return affected

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[衰减] 批量衰减失败: {e}", exc_info=True)
            return -1

    async def _update_access_time_internal(self, memory_id: int) -> bool:
        """立即写入单条记忆的访问时间与次数（供显式更新入口使用）。"""
        return await self._write_access_updates([int(memory_id)], [])

    async def _update_access_times_internal(
        self, doc_ids: list[int], atom_ids=None
    ) -> bool:
        """合并同一时间窗口内的召回访问统计写。

        访问时间/次数只用于衰减排序，短时间内合并写不会改变语义；真正落库由
        `_flush_access_updates` 用单条原子 SQL 完成，避免每次召回都改写 k 行 metadata。

        Args:
            doc_ids: Document ids to bump.
            atom_ids: Optional atom ids to bump.

        Returns:
            bool: True if the ids were queued.
        """
        if self.db_connection is None:
            return False
        self._access_update_pending.update(int(doc_id) for doc_id in doc_ids)
        if atom_ids:
            self._access_atom_update_pending.update(int(atom_id) for atom_id in atom_ids)
        if not self._access_update_pending:
            return False
        if self._access_flush_task is None or self._access_flush_task.done():
            self._access_flush_task = self._create_tracked_task(
                self._deferred_flush_access_updates()
            )
        return True

    async def _deferred_flush_access_updates(self) -> None:
        await asyncio.sleep(self._ACCESS_UPDATE_DEBOUNCE_SECONDS)
        await self._flush_access_updates()

    async def _flush_access_updates(self) -> None:
        """把挂起的访问统计写入数据库（失败时保留挂起项，下次重试）。"""
        doc_ids = sorted(self._access_update_pending)
        atom_ids = sorted(self._access_atom_update_pending)
        if not doc_ids and not atom_ids:
            return
        written = await self._write_access_updates(doc_ids, atom_ids)
        if written:
            self._access_update_pending.difference_update(doc_ids)
            self._access_atom_update_pending.difference_update(atom_ids)

    async def _write_access_updates(self, doc_ids: list[int], atom_ids: list[int]) -> bool:
        """单条原子 SQL 批量递增访问时间与次数。"""
        unique_ids = list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
        if not unique_ids or self.db_connection is None:
            return False

        current_time = time.time()
        try:
            # 单条原子 SQL，避免并发召回任务对同一记忆产生丢失更新
            placeholders = ",".join("?" * len(unique_ids))
            cursor = await self.db_connection.execute(
                f"""
                UPDATE documents
                SET metadata = CASE
                    WHEN json_valid(metadata) THEN json_set(
                        json_set(metadata, '$.last_access_time', ?),
                        '$.access_count',
                        MIN(
                            COALESCE(
                                CAST(json_extract(metadata, '$.access_count') AS INTEGER),
                                0
                            ) + 1,
                            1000000
                        )
                    )
                    ELSE json_set('{{}}', '$.last_access_time', ?, '$.access_count', 1)
                END
                WHERE id IN ({placeholders})
                """,
                (current_time, current_time, *unique_ids),
            )
            if atom_ids and self.atom_store is not None:
                unique_atoms = list(dict.fromkeys(int(atom_id) for atom_id in atom_ids))
                await self.db_connection.execute(
                    f"UPDATE memory_atoms SET last_accessed_at = ? "
                    f"WHERE id IN ({','.join('?' for _ in unique_atoms)}) "
                    "AND status = 'active' AND expires_at > ?",
                    (current_time, *unique_atoms, current_time),
                )
            await self.db_connection.commit()
            return cursor.rowcount > 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 记录错误但不影响查询流程
            logger.warning(
                f"批量更新访问时间失败 (doc_ids={unique_ids}): {e}",
                exc_info=True,
            )
            return False

    async def get_session_memories(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        获取会话的所有记忆（使用分批处理和数据库排序优化）

        Args:
            session_id: 会话ID(应传入完整的unified_msg_origin格式)
            limit: 限制数量

        Returns:
            List[Dict]: 记忆列表
        """
        # 【关键修改】不再提取UUID，直接使用完整的session_id进行匹配
        # 因为现在数据库中存储的就是完整的unified_msg_origin格式

        # 使用数据库层面的过滤、排序和分页，避免加载所有数据
        try:
            if self.db_connection is None:
                return []

            cursor = await self.db_connection.execute(
                """
                SELECT id, text, metadata
                FROM documents
                WHERE json_extract(metadata, '$.session_id') = ?
                ORDER BY CAST(json_extract(metadata, '$.create_time') AS REAL) DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = await cursor.fetchall()

            parsed = await asyncio.to_thread(
                lambda: [safe_json_dict(r["metadata"]) for r in rows]
            )

            memories = []
            for row, metadata in zip(rows, parsed):
                memories.append(
                    {
                        "id": int(row["id"]),
                        "text": row["text"],
                        "metadata": metadata,
                    }
                )

            return memories
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                f"[MemoryEngine] 获取会话记忆失败 (session_id={session_id})",
                exc_info=True,
            )
            return []
