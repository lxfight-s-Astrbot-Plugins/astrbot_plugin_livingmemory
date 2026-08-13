"""
记忆管理处理模块
"""

import inspect
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
from quart import request

from astrbot.api import logger

from ..memory_source import restore_source_messages
from ..memory_transfer import (
    MAX_IMPORT_ENTRIES,
    memory_import_key,
    parse_transfer_content,
    serialize_transfer_csv,
    serialize_transfer_json,
)

if TYPE_CHECKING:
    from .utils import PageApiUtils


class MemoryHandler:
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

    async def export_memories(self, memory_engine) -> dict[str, Any]:
        """Export all or selected memories as portable JSON/CSV content."""
        payload = await request.get_json(silent=True) or {}
        export_format = str(payload.get("format", "json")).strip().lower()
        if export_format not in {"json", "csv"}:
            return self.utils.error("导出格式仅支持 JSON 或 CSV")

        raw_ids = payload.get("memory_ids")
        memory_ids: list[int] | None = None
        if raw_ids is not None:
            if not isinstance(raw_ids, list):
                return self.utils.error("memory_ids 必须是整数列表")
            try:
                memory_ids = [int(item) for item in raw_ids]
            except (TypeError, ValueError):
                return self.utils.error("memory_ids 必须是整数列表")
            if len(memory_ids) > MAX_IMPORT_ENTRIES:
                return self.utils.error(
                    f"单次最多导出 {MAX_IMPORT_ENTRIES} 条记忆"
                )

        records = await memory_engine.get_memory_transfer_records(memory_ids)
        exported_at = datetime.now(timezone.utc).isoformat()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if export_format == "csv":
            content = serialize_transfer_csv(records)
            mime_type = "text/csv;charset=utf-8"
        else:
            content = serialize_transfer_json(records, exported_at)
            mime_type = "application/json;charset=utf-8"

        return self.utils.ok(
            {
                "filename": f"livingmemory-export-{stamp}.{export_format}",
                "mime_type": mime_type,
                "content": content,
                "memory_count": len(records),
            }
        )

    async def import_memories(
        self, memory_engine, memory_processor
    ) -> dict[str, Any]:
        """Preview or import native and common external JSON/CSV memories."""
        payload = await request.get_json(silent=True) or {}
        import_format = str(payload.get("format", "json")).strip().lower()
        content = payload.get("content")
        dry_run = bool(payload.get("dry_run", True))
        duplicate_strategy = str(
            payload.get("duplicate_strategy", "skip")
        ).strip().lower()
        if duplicate_strategy not in {"skip", "allow"}:
            return self.utils.error("duplicate_strategy 仅支持 skip 或 allow")
        if not isinstance(content, str) or not content.strip():
            return self.utils.error("导入文件内容不能为空")
        if len(content.encode("utf-8")) > 50 * 1024 * 1024:
            return self.utils.error("导入文件不能超过 50 MiB")

        try:
            entries, parse_errors = parse_transfer_content(content, import_format)
        except ValueError as exc:
            return self.utils.error(str(exc))

        existing_keys = await memory_engine.get_memory_import_keys()
        detected_keys = set(existing_keys)
        duplicate_count = 0
        for entry in entries:
            if not entry.content:
                continue
            key = memory_import_key(
                entry.content, entry.session_id, entry.persona_id
            )
            if key in detected_keys:
                duplicate_count += 1
            detected_keys.add(key)

        summary_required = sum(entry.requires_summary for entry in entries)
        if dry_run:
            planned_count = len(entries)
            if duplicate_strategy == "skip":
                planned_count -= duplicate_count
            return self.utils.ok(
                {
                    "dry_run": True,
                    "total": len(entries) + len(parse_errors),
                    "valid_count": len(entries),
                    "invalid_count": len(parse_errors),
                    "duplicate_count": duplicate_count,
                    "summary_required_count": summary_required,
                    "planned_import_count": max(0, planned_count),
                    "errors": parse_errors[:50],
                }
            )

        imported_ids: list[int] = []
        skipped_duplicates = 0
        import_errors = list(parse_errors)
        active_keys = set(existing_keys)
        for entry in entries:
            try:
                content_text = entry.content
                metadata = dict(entry.metadata)
                importance = entry.importance
                if entry.requires_summary:
                    if memory_processor is None:
                        raise RuntimeError(
                            "记忆处理器未初始化，无法总结仅含原始对话的导入项"
                        )
                    messages = restore_source_messages(entry.source_messages)
                    generated_content, generated_metadata, generated_importance = (
                        await memory_processor.process_conversation(
                            messages=messages,
                            is_group_chat=bool(messages[0].group_id),
                            persona_id=entry.persona_id,
                        )
                    )
                    content_text = generated_content
                    metadata.update(generated_metadata or {})
                    importance = generated_importance

                key = memory_import_key(
                    content_text, entry.session_id, entry.persona_id
                )
                if duplicate_strategy == "skip" and key in active_keys:
                    skipped_duplicates += 1
                    continue

                metadata["memory_origin"] = "memory_import"
                metadata["imported_at"] = time.time()
                metadata["import_source_index"] = entry.source_index
                importance = self._normalize_importance_update(
                    importance, "stored"
                )
                metadata["importance"] = importance
                atoms = None
                classify_atoms = getattr(
                    memory_processor, "classify_atoms_from_metadata", None
                )
                if callable(classify_atoms):
                    atoms = classify_atoms(
                        metadata=metadata,
                        parent_importance=importance,
                        session_id=entry.session_id,
                        persona_id=entry.persona_id,
                    )
                memory_id = await memory_engine.add_memory(
                    content=content_text,
                    session_id=entry.session_id,
                    persona_id=entry.persona_id,
                    importance=importance,
                    metadata=metadata,
                    atoms=atoms,
                    preserve_create_time="create_time" in metadata,
                    source_messages=entry.source_messages or None,
                )
                imported_ids.append(memory_id)
                active_keys.add(key)
            except Exception as exc:
                logger.error(
                    f"[PageAPI] 导入记忆失败 (index={entry.source_index}): {exc}",
                    exc_info=True,
                )
                import_errors.append(
                    {"index": entry.source_index, "error": str(exc)}
                )

        return self.utils.ok(
            {
                "dry_run": False,
                "total": len(entries) + len(parse_errors),
                "imported_count": len(imported_ids),
                "skipped_duplicate_count": skipped_duplicates,
                "failed_count": len(import_errors),
                "imported_ids": imported_ids,
                "errors": import_errors[:50],
            }
        )

    async def update_memory(self, memory_engine) -> dict[str, Any]:
        """
        更新单个记忆的字段

        支持的字段:
            - content: 记忆内容（会创建新记忆并删除旧记忆）
            - topics: 主题列表（会重建记忆及派生数据）
            - key_facts: 关键事实列表（会重建记忆及派生数据）
            - structured: 联合更新上述字段及其他元数据
            - importance: 重要性（0-1 或 0-10）
            - status: 状态（active/archived/deleted）
            - type: 类型

        Payload:
            - memory_id: 记忆ID（必需）
            - field: 要更新的字段（必需）
            - value: 新值（必需）
            - reason: 更新原因（可选）

        Returns:
            包含更新结果的字典
        """
        from ..utils.number_utils import clamp_float

        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")

        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        value_scale = str(payload.get("value_scale", "auto")).strip().lower()
        reason = str(payload.get("reason", "")).strip()

        if not field or value is None:
            return self.utils.error("需要指定 field 和 value")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")

        current_metadata = self.utils.normalize_metadata(memory.get("metadata"))

        # 内容、主题和关键事实共同决定向量、原子与图数据，必须一次重建。
        if field in {"content", "topics", "key_facts", "structured"}:
            if field == "structured":
                if not isinstance(value, dict):
                    return self.utils.error("structured value 必须是对象")
                structured = value
            else:
                structured = {field: value}

            new_content = str(
                structured.get("content", memory.get("text", ""))
            ).strip()
            if not new_content:
                return self.utils.error("记忆内容不能为空")

            try:
                topics = self._normalize_edit_list(
                    structured.get("topics", current_metadata.get("topics", [])),
                    "topics",
                )
                key_facts = self._normalize_edit_list(
                    structured.get(
                        "key_facts", current_metadata.get("key_facts", [])
                    ),
                    "key_facts",
                )
                importance = (
                    self._normalize_importance_update(
                        structured["importance"], value_scale
                    )
                    if "importance" in structured
                    else clamp_float(
                        current_metadata.get("importance"), default=0.5
                    )
                )
            except ValueError as exc:
                return self.utils.error(str(exc))

            status_value = str(
                structured.get("status", current_metadata.get("status", "active"))
            ).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self.utils.error("状态必须是 active、archived 或 deleted")
            type_value = str(
                structured.get(
                    "type", current_metadata.get("memory_type", "GENERAL")
                )
            ).strip()
            if not type_value:
                return self.utils.error("类型不能为空")

            updated_at = time.time()
            replacement_metadata = current_metadata.copy()
            changes = {
                "content": (str(memory.get("text", "")), new_content),
                "topics": (current_metadata.get("topics", []), topics),
                "key_facts": (current_metadata.get("key_facts", []), key_facts),
                "status": (current_metadata.get("status", "active"), status_value),
                "type": (
                    current_metadata.get("memory_type", "GENERAL"),
                    type_value,
                ),
                "importance": (
                    clamp_float(current_metadata.get("importance"), default=0.5),
                    importance,
                ),
            }
            changed_fields = [
                name for name, (old, new) in changes.items() if old != new
            ]
            if not changed_fields:
                return self.utils.ok(
                    {
                        "message": "没有检测到修改",
                        "memory_id": memory_id,
                        "new_memory_id": memory_id,
                        "field": field,
                    }
                )

            for changed_field in changed_fields:
                old_value, new_value = changes[changed_field]
                replacement_metadata["update_history"] = (
                    self.utils.append_update_history(
                        replacement_metadata,
                        field=changed_field,
                        old_value=old_value,
                        new_value=new_value,
                        reason=reason,
                        timestamp=updated_at,
                    )
                )

            if reason:
                replacement_metadata["update_reason"] = reason
            replacement_metadata.update(
                {
                    "updated_at": updated_at,
                    "topics": topics,
                    "key_facts": key_facts,
                    "status": status_value,
                    "memory_type": type_value,
                    "importance": importance,
                }
            )
            if "content" in changed_fields:
                replacement_metadata["previous_content"] = str(
                    memory.get("text", "")
                )[:100]
                replacement_metadata["canonical_summary"] = new_content
                replacement_metadata["persona_summary"] = new_content

            try:
                new_memory_id = await memory_engine.replace_memory(
                    memory_id,
                    content=new_content,
                    importance=importance,
                    metadata=replacement_metadata,
                )
            except Exception as exc:
                logger.error(f"[PageAPI] 重建记忆失败: {exc}", exc_info=True)
                return self.utils.error(str(exc))

            return self.utils.ok(
                {
                    "message": f"记忆已更新（ID: {memory_id} → {new_memory_id}）",
                    "old_memory_id": memory_id,
                    "new_memory_id": new_memory_id,
                    "field": field,
                    "changed_fields": changed_fields,
                }
            )

        # 其他字段更新
        updates: dict[str, Any] = {}
        old_value_for_history: Any
        new_value_for_history: Any
        if field == "importance":
            try:
                normalized = self._normalize_importance_update(value, value_scale)
            except ValueError as exc:
                return self.utils.error(str(exc))
            updates["importance"] = normalized
            old_value_for_history = self.utils.importance_to_display(
                current_metadata.get("importance", 0.5)
            )
            new_value_for_history = round(normalized * 10.0, 2)
        elif field == "status":
            status_value = str(value).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self.utils.error("状态必须是 active、archived 或 deleted")
            current_status = str(current_metadata.get("status") or "active")
            if status_value == current_status:
                return self.utils.ok(
                    {
                        "message": "没有检测到修改",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            if status_value == "archived":
                archived = await memory_engine.archive_memories([memory_id])
                if archived != 1:
                    return self.utils.error("归档失败")
                return self.utils.ok(
                    {
                        "message": "记忆已归档并移出检索索引",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            if status_value == "active" and current_status == "archived":
                if not await memory_engine.restore_memory(memory_id):
                    return self.utils.error("恢复失败")
                return self.utils.ok(
                    {
                        "message": "记忆已恢复并重建检索索引",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            updates["metadata"] = {"status": status_value}
            old_value_for_history = current_metadata.get("status", "active")
            new_value_for_history = status_value
        elif field == "type":
            type_value = str(value).strip()
            if not type_value:
                return self.utils.error("类型不能为空")
            updates["metadata"] = {"memory_type": type_value}
            old_value_for_history = current_metadata.get("memory_type", "GENERAL")
            new_value_for_history = type_value
        else:
            return self.utils.error(f"不支持编辑字段: {field}")

        updated_at = time.time()
        updates.setdefault("metadata", {})
        updates["metadata"]["update_history"] = self.utils.append_update_history(
            current_metadata,
            field=field,
            old_value=old_value_for_history,
            new_value=new_value_for_history,
            reason=reason,
            timestamp=updated_at,
        )
        updates["metadata"]["updated_at"] = updated_at

        if reason:
            updates["metadata"]["update_reason"] = reason

        try:
            success = await memory_engine.update_memory(memory_id, updates)
        except Exception as exc:
            logger.error(f"[PageAPI] 更新记忆失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))

        if not success:
            return self.utils.error("更新失败")

        return self.utils.ok(
            {
                "message": f"记忆 {memory_id} 的 {field} 已更新",
                "memory_id": memory_id,
                "field": field,
            }
        )

    async def batch_delete_memories(self, memory_engine) -> dict[str, Any]:
        """
        批量删除记忆

        Payload:
            - memory_ids: 记忆ID列表（必需）

        Returns:
            包含删除统计的字典
        """
        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        if not isinstance(memory_ids, list) or not memory_ids:
            return self.utils.error("需要提供记忆 ID 列表")

        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []

        valid_ids: list[int] = []
        for raw_id in memory_ids:
            try:
                valid_ids.append(int(raw_id))
            except Exception:
                failed_count += 1
                failed_ids.append(raw_id)

        if valid_ids:
            deleted_count = await memory_engine.batch_delete_memories(valid_ids)

        return self.utils.ok(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_update_memories(self, memory_engine) -> dict[str, Any]:
        """
        批量更新记忆字段

        支持的字段:
            - status: 状态
            - importance: 重要性
            - type: 类型

        Payload:
            - memory_ids: 记忆ID列表（必需）
            - field: 要更新的字段（必需）
            - value: 新值（必需）

        Returns:
            包含更新统计的字典
        """
        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        value_scale = str(payload.get("value_scale", "auto")).strip().lower()

        if not isinstance(memory_ids, list) or not memory_ids:
            return self.utils.error("需要提供记忆 ID 列表")
        if not field or value is None:
            return self.utils.error("需要指定 field 和 value")

        if field not in ("status", "importance", "type"):
            return self.utils.error(f"批量更新不支持字段: {field}")

        updated_count = 0
        failed_ids: list[Any] = []

        for raw_id in memory_ids:
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue

            try:
                updates: dict[str, Any] = {}
                if field == "status":
                    status_value = str(value).strip()
                    if status_value not in {"active", "archived", "deleted"}:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"status": status_value}
                elif field == "importance":
                    try:
                        updates["importance"] = self._normalize_importance_update(
                            value, value_scale
                        )
                    except ValueError:
                        failed_ids.append(raw_id)
                        continue
                elif field == "type":
                    type_value = str(value).strip()
                    if not type_value:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"memory_type": type_value}

                success = await memory_engine.update_memory(memory_id, updates)
                if success:
                    updated_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception:
                failed_ids.append(raw_id)

        return self.utils.ok(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
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
