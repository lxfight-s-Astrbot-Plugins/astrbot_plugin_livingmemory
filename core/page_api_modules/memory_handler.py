"""
记忆管理处理模块
"""

import inspect
from typing import TYPE_CHECKING, Any

import aiosqlite
from quart import request

from astrbot.api import logger

from ..memory_source import restore_source_messages
from .memory_handler_update import MemoryHandlerUpdateMixin
from .memory_handler_io import MemoryHandlerIoMixin

if TYPE_CHECKING:
    from .utils import PageApiUtils

class MemoryHandler(MemoryHandlerUpdateMixin, MemoryHandlerIoMixin):
    """记忆管理处理器"""

    def __init__(self, utils: "PageApiUtils"):
        """
        初始化记忆管理处理器

        Args:
            utils: PageApiUtils 工具实例
        """
        self.utils = utils

    @staticmethod
    def _normalize_importance_update(value: Any, value_scale: str = "auto") -> float:
        """Normalize WebUI/API importance input into the stored 0-1 scale."""
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("重要性必须是数字") from exc

        scale = str(value_scale or "auto").strip().lower()
        if scale in {"display", "0-10", "ten"}:
            if not 0.0 <= parsed <= 10.0:
                raise ValueError("重要性必须在 0-10 范围内")
            return parsed / 10.0

        if scale in {"stored", "normalized", "0-1"}:
            if not 0.0 <= parsed <= 1.0:
                raise ValueError("重要性必须在 0-1 范围内")
            return parsed

        if 0.0 <= parsed <= 1.0:
            return parsed
        if 0.0 <= parsed <= 10.0:
            return parsed / 10.0

        raise ValueError("重要性必须在 0-1 或 0-10 范围内")

    @staticmethod
    def _normalize_edit_list(value: Any, field: str) -> list[str]:
        """Validate a manually edited topics/key_facts list."""
        if not isinstance(value, list):
            raise ValueError(f"{field} 必须是字符串列表")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{field} 必须是字符串列表")
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)

        if len(normalized) > 5:
            raise ValueError(f"{field} 最多允许 5 项")
        return normalized

    async def list_memories(self, memory_engine) -> dict[str, Any]:
        """
        获取记忆列表（带分页和过滤）

        查询参数:
            - session_id: 会话ID过滤
            - keyword: 关键词搜索（支持ID或文本）
            - status: 状态过滤（all/active/archived）
            - type: 记忆类型过滤（all/GENERAL/FACT/PREFERENCE/...）
            - sort: 排序方式（created_desc/created_asc/updated_desc/...）
            - page: 页码（默认1）
            - page_size: 每页数量（默认20，最大500）

        Returns:
            包含记忆列表和分页信息的字典
        """
        query = request.args
        session_id = self.utils.optional_text(query.get("session_id"))
        keyword = str(query.get("keyword", "")).strip()
        status_filter = str(query.get("status", "all")).strip().lower() or "all"
        type_filter = self.utils.optional_text(query.get("type"))
        if type_filter and type_filter.lower() == "all":
            type_filter = None
        sort_key = str(query.get("sort", "created_desc")).strip().lower()

        try:
            page = max(1, int(query.get("page", 1)))
            page_size = min(500, max(1, int(query.get("page_size", 20))))
        except (TypeError, ValueError):
            return self.utils.error("分页参数无效")

        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            return self.utils.error("MemoryEngine db_path unavailable")

        offset = (page - 1) * page_size
        where_clauses: list[str] = []
        params: list[Any] = []
        type_expr = (
            "UPPER(COALESCE("
            "CASE WHEN json_valid(metadata) "
            "THEN json_extract(metadata, '$.memory_type') END,"
            "'GENERAL'"
            "))"
        )

        if session_id:
            where_clauses.append(
                "CASE WHEN json_valid(metadata) "
                "THEN json_extract(metadata, '$.session_id') END = ?"
            )
            params.append(session_id)

        if status_filter != "all":
            where_clauses.append(
                "COALESCE("
                "CASE WHEN json_valid(metadata) "
                "THEN json_extract(metadata, '$.status') END,"
                "'active'"
                ") = ?"
            )
            params.append(status_filter)

        if type_filter:
            where_clauses.append(f"{type_expr} = ?")
            params.append(type_filter.upper())

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
                    "OR COALESCE("
                    "CASE WHEN json_valid(metadata) "
                    "THEN json_extract(metadata, '$.memory_type') END,"
                    "''"
                    ") LIKE ? COLLATE NOCASE"
                    ")"
                )
                params.extend([keyword_like, keyword_like])

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        created_expr = (
            "COALESCE("
            "CASE WHEN json_valid(metadata) "
            "THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,"
            "0)"
        )
        updated_expr = (
            "COALESCE("
            "CASE WHEN json_valid(metadata) "
            "THEN CAST(json_extract(metadata, '$.updated_at') AS REAL) END,"
            "CASE WHEN json_valid(metadata) "
            "THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,"
            "0)"
        )
        importance_raw_expr = (
            "COALESCE("
            "CASE WHEN json_valid(metadata) "
            "THEN CAST(json_extract(metadata, '$.importance') AS REAL) END,"
            "0.5)"
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

        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row

                count_cursor = await db.execute(
                    f"SELECT COUNT(*) AS total FROM documents {where_clause}",
                    params,
                )
                count_row = await count_cursor.fetchone()
                total = int(count_row["total"]) if count_row else 0

                cursor = await db.execute(
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
        except Exception as exc:
            logger.error(f"[PageAPI] 获取记忆列表失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "doc_id": row["doc_id"],
                    "text": row["text"],
                    "metadata": self.utils.normalize_metadata(row["metadata"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return self.utils.ok(
            {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": (offset + page_size) < total,
                "filters": {
                    "session_id": session_id,
                    "keyword": keyword,
                    "status": status_filter,
                    "type": type_filter,
                },
                "sort": sort_key,
            }
        )

    async def get_memory_detail(self, memory_engine) -> dict[str, Any]:
        """
        获取单个记忆的完整详情

        查询参数:
            - memory_id: 记忆ID（必需）

        Returns:
            包含记忆详情和相关图谱上下文的字典
        """
        from ..utils.number_utils import clamp_float

        query = request.args
        try:
            memory_id = int(query.get("memory_id", ""))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")

        metadata = self.utils.normalize_metadata(memory.get("metadata"))

        # 构建完整的详情数据
        get_source = getattr(memory_engine, "get_memory_source", None)
        source_result = get_source(memory_id) if callable(get_source) else []
        source_messages = (
            await source_result if inspect.isawaitable(source_result) else []
        )
        detail = {
            "memory_id": memory.get("id"),
            "doc_id": memory.get("doc_id"),
            "text": memory.get("text"),
            "summary": (
                metadata.get("persona_summary")
                or metadata.get("canonical_summary")
                or memory.get("text", "")
            ),
            "created_at": memory.get("created_at"),
            "updated_at": memory.get("updated_at"),
            "metadata": metadata,
            "memory_type": metadata.get("memory_type", "GENERAL"),
            "importance": clamp_float(metadata.get("importance"), default=0.5),
            "status": metadata.get("status", "active"),
            "session_id": metadata.get("session_id"),
            "persona_id": metadata.get("persona_id"),
            "key_facts": metadata.get("key_facts", []),
            "topics": metadata.get("topics", []),
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "update_history": metadata.get("update_history", []),
            "source_messages": source_messages,
            "consolidated_from": metadata.get("consolidated_from", []),
            "consolidated_at": metadata.get("consolidated_at"),
        }

        # 附加相关的图谱子图
        graph_store = self.utils.get_graph_store(memory_engine)
        if graph_store is not None:
            try:
                subgraph = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=20,
                    limit_nodes=20,
                    limit_edges=30,
                )
                detail["graph_context"] = {
                    "nodes": subgraph.get("nodes", []),
                    "edges": subgraph.get("edges", []),
                    "entries": subgraph.get("entries", []),
                }
            except Exception:
                detail["graph_context"] = None
        else:
            detail["graph_context"] = None

        return self.utils.ok(detail)

    async def resummarize_memory(
        self, memory_engine, memory_processor
    ) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")
        if memory_processor is None:
            return self.utils.error("记忆处理器未初始化")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")
        get_source = getattr(memory_engine, "get_memory_source", None)
        source_result = get_source(memory_id) if callable(get_source) else []
        source = await source_result if inspect.isawaitable(source_result) else []
        if len(source) < 2:
            return self.utils.error("该记忆没有可重新总结的完整原文")

        messages = restore_source_messages(source)
        current_metadata = self.utils.normalize_metadata(memory.get("metadata"))
        is_group_chat = bool(messages[0].group_id if messages else False)
        try:
            content, metadata, importance = await memory_processor.process_conversation(
                messages=messages,
                is_group_chat=is_group_chat,
                persona_id=current_metadata.get("persona_id"),
            )
            metadata["source_window"] = {
                **(current_metadata.get("source_window") or {}),
                "resummarized_from": memory_id,
                "message_count": len(messages),
            }
            metadata["memory_origin"] = "source_resummarization"
            new_memory_id = await memory_engine.replace_memory(
                memory_id,
                content=content,
                importance=importance,
                metadata={**current_metadata, **metadata},
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 重新总结记忆失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))

        return self.utils.ok(
            {
                "message": "记忆已根据原文重新总结",
                "old_memory_id": memory_id,
                "new_memory_id": new_memory_id,
            }
        )

    async def _get_memory_record(
        self, memory_id: int, memory_engine
    ) -> dict[str, Any] | None:
        """
        获取单个记忆的原始记录

        Args:
            memory_id: 记忆ID
            memory_engine: 记忆引擎实例

        Returns:
            记忆记录字典，如果不存在则返回 None
        """
        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            return None

        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
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
        except Exception as exc:
            logger.error(f"[PageAPI] 获取记忆记录失败: {exc}", exc_info=True)
            return None
