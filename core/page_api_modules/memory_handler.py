"""
记忆管理处理模块
"""

import asyncio
import hashlib
import json
import time
import uuid
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

import aiosqlite
from quart import request

from astrbot.api import logger

from ..processors.entity_resolver import EntityResolver

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
        self._update_plans: dict[str, dict[str, Any]] = {}
        self._update_jobs: dict[str, dict[str, Any]] = {}
        self._update_tasks: dict[str, asyncio.Task] = {}

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
        detail = {
            "memory_id": memory.get("id"),
            "doc_id": memory.get("doc_id"),
            "text": memory.get("text"),
            "summary": metadata.get("canonical_summary") or memory.get("text", ""),
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
            "participants": metadata.get("participants", []),
            "persona_summary": metadata.get("persona_summary", ""),
            "sentiment": metadata.get("sentiment", "neutral"),
            "revision": metadata.get("revision", 1),
            "memory_uid": metadata.get("memory_uid"),
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "update_history": metadata.get("update_history", []),
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

    @staticmethod
    def _normalize_text_list(value: Any, limit: int = 20) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", "\n").splitlines()
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = str(item).strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
            if len(result) >= limit:
                break
        return result

    async def _replace_structured_memory(
        self,
        memory_engine,
        memory_processor,
        memory: dict[str, Any],
        structured_value: dict[str, Any],
        reason: str,
        update_mode: str = "rebuild",
    ) -> dict[str, Any]:
        if memory_processor is None:
            return self.utils.error("MemoryProcessor 未初始化")

        memory_id = int(memory["id"])
        current_metadata = self.utils.normalize_metadata(memory.get("metadata"))
        session_id = current_metadata.get("session_id")
        persona_id = current_metadata.get("persona_id")
        is_group_chat = (
            current_metadata.get("interaction_type") == "group_chat"
            or "GroupMessage" in str(session_id or "")
        )

        summary = str(structured_value.get("summary", "")).strip()
        if not summary:
            return self.utils.error("记忆摘要不能为空")
        try:
            importance = self._normalize_importance_update(
                structured_value.get("importance", current_metadata.get("importance", 0.5)),
                str(structured_value.get("importance_scale", "stored")),
            )
        except ValueError as exc:
            return self.utils.error(str(exc))

        structured_data = {
            "summary": summary,
            "topics": self._normalize_text_list(structured_value.get("topics"), 10),
            "key_facts": self._normalize_text_list(
                structured_value.get("key_facts"), 20
            ),
            "participants": self._normalize_text_list(
                structured_value.get("participants"), 30
            ),
            "sentiment": str(
                structured_value.get(
                    "sentiment", current_metadata.get("sentiment", "neutral")
                )
            ),
            "importance": importance,
        }
        status = str(
            structured_value.get("status", current_metadata.get("status", "active"))
        ).strip() or "active"
        if status not in {"active", "archived", "deleted"}:
            return self.utils.error("状态必须是 active、archived 或 deleted")

        try:
            content, generated_metadata, normalized_importance = (
                memory_processor.build_memory_from_structured_data(
                    structured_data=structured_data,
                    is_group_chat=is_group_chat,
                    fallback_excerpt=summary,
                )
            )

            # Preserve operational provenance while replacing every field that can
            # affect retrieval, graph extraction, atoms, or prompt injection.
            replacement_metadata = dict(current_metadata)
            replacement_metadata.update(generated_metadata)
            # The standard builder omits participants for private chats, so assign
            # explicitly to ensure stale values cannot survive a structured edit.
            replacement_metadata["participants"] = structured_data["participants"]
            persona_summary = str(
                structured_value.get("persona_summary", summary)
            ).strip()
            replacement_metadata["persona_summary"] = persona_summary or summary
            replacement_metadata["memory_type"] = str(
                structured_value.get(
                    "memory_type", current_metadata.get("memory_type", "GENERAL")
                )
            ).strip() or "GENERAL"
            replacement_metadata["status"] = status
        except Exception as exc:
            logger.error(f"[PageAPI] 重建结构化记忆失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))
        replacement_metadata["update_history"] = self.utils.append_update_history(
            current_metadata,
            field="structured",
            old_value={
                "content": memory.get("text", ""),
                "topics": current_metadata.get("topics", []),
                "key_facts": current_metadata.get("key_facts", []),
            },
            new_value={
                "content": content,
                "topics": replacement_metadata.get("topics", []),
                "key_facts": replacement_metadata.get("key_facts", []),
            },
            reason=reason,
            timestamp=time.time(),
        )
        if reason:
            replacement_metadata["update_reason"] = reason

        if update_mode not in {"rebuild", "in_place"}:
            return self.utils.error("update_mode 必须是 rebuild 或 in_place")
        replacement_metadata["last_update_mode"] = update_mode

        try:
            atoms = memory_processor.classify_atoms_from_metadata(
                metadata=replacement_metadata,
                parent_importance=normalized_importance,
                session_id=session_id,
                persona_id=persona_id,
            )
            if update_mode == "in_place":
                new_memory_id = await memory_engine.rewrite_memory_in_place(
                    memory_id,
                    content=content,
                    metadata=replacement_metadata,
                    importance=normalized_importance,
                    atoms=atoms,
                )
            else:
                new_memory_id = await memory_engine.replace_memory(
                    memory_id,
                    content=content,
                    metadata=replacement_metadata,
                    importance=normalized_importance,
                    atoms=atoms,
                )
        except Exception as exc:
            logger.error(f"[PageAPI] 结构化更新记忆失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))

        return self.utils.ok(
            {
                "message": (
                    f"结构化记忆已原位重建（ID: {memory_id}）"
                    if update_mode == "in_place"
                    else f"结构化记忆已更新（ID: {memory_id} → {new_memory_id}）"
                ),
                "old_memory_id": memory_id,
                "new_memory_id": new_memory_id,
                "field": "structured",
                "update_mode": update_mode,
            }
        )

    @staticmethod
    def _scope_matches(
        source_metadata: dict[str, Any],
        candidate_metadata: dict[str, Any],
        scope: str,
    ) -> bool:
        if scope == "session":
            source_session = source_metadata.get("session_id")
            return (
                bool(source_session)
                and candidate_metadata.get("session_id") == source_session
            )
        if scope == "persona":
            source_persona = source_metadata.get("persona_id")
            return (
                bool(source_persona)
                and candidate_metadata.get("persona_id") == source_persona
            )
        return False

    def _structured_memory_value(
        self,
        memory: dict[str, Any], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the editable representation used by structured rebuilds."""
        return {
            "summary": metadata.get("persona_summary")
            or metadata.get("canonical_summary")
            or memory.get("text", ""),
            "persona_summary": metadata.get("persona_summary")
            or metadata.get("canonical_summary")
            or memory.get("text", ""),
            "topics": self._normalize_text_list(metadata.get("topics"), 30),
            "key_facts": self._normalize_text_list(metadata.get("key_facts"), 30),
            "participants": self._normalize_text_list(
                metadata.get("participants"), 30
            ),
            "sentiment": metadata.get("sentiment", "neutral"),
            "importance": metadata.get("importance", 0.5),
            "importance_scale": "stored",
            "memory_type": metadata.get("memory_type", "GENERAL"),
            "status": metadata.get("status", "active"),
        }

    def _memory_fingerprint(self, memory: dict[str, Any]) -> str:
        metadata = self.utils.normalize_metadata(memory.get("metadata"))
        relevant = {
            "id": memory.get("id"),
            "text": memory.get("text", ""),
            "revision": metadata.get("revision", 1),
            "canonical_summary": metadata.get("canonical_summary"),
            "persona_summary": metadata.get("persona_summary"),
            "topics": metadata.get("topics", []),
            "key_facts": metadata.get("key_facts", []),
            "participants": metadata.get("participants", []),
            "sentiment": metadata.get("sentiment"),
            "importance": metadata.get("importance"),
            "memory_type": metadata.get("memory_type"),
            "status": metadata.get("status"),
        }
        payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _derive_propagating_changes(
        self,
        metadata: dict[str, Any],
        edited: dict[str, Any],
        declared_changes: Any = None,
    ) -> list[dict[str, Any]]:
        """Find unambiguous replacements; additions and removals never propagate.

        The WebUI declares row-level operations explicitly. The positional fallback
        is retained for older API clients and only accepts conservative replacements.
        """
        changes: list[dict[str, Any]] = []
        if isinstance(declared_changes, list):
            for declared in declared_changes:
                if not isinstance(declared, dict):
                    continue
                # Add/remove operations intentionally never propagate.
                if str(declared.get("operation", "")).strip() != "replace":
                    continue
                field = str(declared.get("field", "")).strip()
                if field not in {"key_facts", "topics"}:
                    continue
                before = str(declared.get("before", "")).strip()
                after = str(declared.get("after", "")).strip()
                old_values = self._normalize_text_list(metadata.get(field), 30)
                new_values = self._normalize_text_list(edited.get(field), 30)
                if before not in old_values or after not in new_values or before == after:
                    continue
                changes.append(
                    {
                        "change_id": uuid.uuid4().hex,
                        "field": field,
                        "operation": "replace",
                        "before": before,
                        "after": after,
                    }
                )
            return changes

        for field in ("key_facts", "topics"):
            old_values = self._normalize_text_list(metadata.get(field), 30)
            new_values = self._normalize_text_list(edited.get(field), 30)
            old_keys = [EntityResolver.canonicalize(item) for item in old_values]
            new_keys = [EntityResolver.canonicalize(item) for item in new_values]
            for index in range(min(len(old_values), len(new_values))):
                before = old_values[index]
                after = new_values[index]
                before_key = old_keys[index]
                after_key = new_keys[index]
                if not before_key or not after_key or before_key == after_key:
                    continue
                # A value found elsewhere means insertion/deletion/reordering may
                # have shifted the textarea rows. Do not infer a replacement.
                if before_key in new_keys or after_key in old_keys:
                    continue
                changes.append(
                    {
                        "change_id": uuid.uuid4().hex,
                        "field": field,
                        "operation": "replace",
                        "before": before,
                        "after": after,
                        "source_index": index,
                    }
                )
        return changes

    async def _list_scope_memories(
        self,
        memory_engine,
        source_metadata: dict[str, Any],
        scope: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            raise RuntimeError("MemoryEngine db_path unavailable")
        field = "session_id" if scope == "session" else "persona_id"
        scope_value = source_metadata.get(field)
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT id, text, metadata
                FROM documents
                WHERE json_valid(metadata)
                  AND json_extract(metadata, '$.{field}') = ?
                  AND COALESCE(json_extract(metadata, '$.status'), 'active') != 'deleted'
                ORDER BY id DESC
                LIMIT ?
                """,
                (scope_value, limit),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": int(row["id"]),
                "text": str(row["text"] or ""),
                "metadata": self.utils.normalize_metadata(row["metadata"]),
            }
            for row in rows
        ]

    @staticmethod
    def _field_match(before: str, candidate: str) -> tuple[str, float] | None:
        if before == candidate:
            return "exact", 1.0
        before_key = EntityResolver.canonicalize(before)
        candidate_key = EntityResolver.canonicalize(candidate)
        if not before_key or not candidate_key:
            return None
        if before_key == candidate_key:
            return "normalized_exact", 1.0
        score = SequenceMatcher(None, before_key, candidate_key, autojunk=False).ratio()
        if score >= 0.86:
            return "near", score
        return None

    def _build_candidate_plan(
        self,
        candidate: dict[str, Any],
        changes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        metadata = self.utils.normalize_metadata(candidate.get("metadata"))
        before_value = self._structured_memory_value(candidate, metadata)
        proposed_value = {
            key: list(value) if isinstance(value, list) else value
            for key, value in before_value.items()
        }
        modifications: list[dict[str, Any]] = []

        for change in changes:
            field = change["field"]
            candidate_values = self._normalize_text_list(proposed_value.get(field), 30)
            best: tuple[int, str, float] | None = None
            for index, candidate_text in enumerate(candidate_values):
                matched = self._field_match(change["before"], candidate_text)
                if matched is None:
                    continue
                match_type, score = matched
                if best is None or score > best[2]:
                    best = (index, match_type, score)
            if best is None:
                continue

            index, match_type, score = best
            candidate_before = candidate_values[index]
            candidate_values[index] = change["after"]
            proposed_value[field] = candidate_values
            summary_before = str(proposed_value.get("summary", ""))
            summary_after = summary_before.replace(candidate_before, change["after"])
            if summary_after != summary_before:
                proposed_value["summary"] = summary_after
                proposed_value["persona_summary"] = summary_after
            modifications.append(
                {
                    "change_id": change["change_id"],
                    "field": field,
                    "operation": "replace",
                    "match_type": match_type,
                    "score": round(score, 6),
                    "source_before": change["before"],
                    "source_after": change["after"],
                    "candidate_before": candidate_before,
                    "candidate_after": change["after"],
                    "summary_changed": summary_after != summary_before,
                    "summary_before": (
                        summary_before if summary_after != summary_before else None
                    ),
                    "summary_after": (
                        summary_after if summary_after != summary_before else None
                    ),
                }
            )

        if not modifications:
            return None
        strongest = min(item["score"] for item in modifications)
        default_selected = all(
            item["match_type"] in {"exact", "normalized_exact"}
            for item in modifications
        )
        return {
            "plan_item_id": uuid.uuid4().hex,
            "memory_id": int(candidate["id"]),
            "memory_fingerprint": self._memory_fingerprint(candidate),
            "excerpt": str(candidate.get("text", ""))[:160],
            "match_score": round(strongest, 6),
            "default_selected": default_selected,
            "modification_type": (
                "exact_replace" if default_selected else "near_replace"
            ),
            "modifications": modifications,
            "before_value": before_value,
            "proposed_value": proposed_value,
        }

    async def detect_related_memories(self, memory_engine) -> dict[str, Any]:
        """Build and freeze a field-level related-memory modification plan."""
        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")

        scope = str(payload.get("scope", "current")).strip().lower()
        if scope not in {"session", "persona"}:
            return self.utils.error("scope 必须是 session 或 persona")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")
        metadata = self.utils.normalize_metadata(memory.get("metadata"))
        if scope == "session" and not metadata.get("session_id"):
            return self.utils.error("当前记忆没有 session_id，无法检测同会话记忆")
        if scope == "persona" and not metadata.get("persona_id"):
            return self.utils.error("当前记忆没有 persona_id，无法检测当前人格记忆")

        edited = payload.get("value") if isinstance(payload.get("value"), dict) else {}
        changes = self._derive_propagating_changes(
            metadata,
            edited,
            payload.get("field_changes"),
        )

        records: list[dict[str, Any]] = []
        if changes:
            try:
                records = await self._list_scope_memories(
                    memory_engine, metadata, scope
                )
            except Exception as exc:
                logger.error(f"[PageAPI] 检测关联记忆失败: {exc}", exc_info=True)
                return self.utils.error(str(exc))

        items: list[dict[str, Any]] = []
        for candidate in records:
            if int(candidate["id"]) == memory_id:
                continue
            planned = self._build_candidate_plan(candidate, changes)
            if planned is not None:
                items.append(planned)
            if len(items) >= 50:
                break

        plan_id = uuid.uuid4().hex
        now = time.time()
        plan = {
            "plan_id": plan_id,
            "source_memory_id": memory_id,
            "source_fingerprint": self._memory_fingerprint(memory),
            "source_value": edited,
            "scope": scope,
            "changes": changes,
            "items": items,
            "created_at": now,
            "updated_at": now,
        }
        self._update_plans[plan_id] = plan
        self._prune_update_jobs()

        return self.utils.ok(
            {
                "plan_id": plan_id,
                "items": items,
                "changes": changes,
                "scope": scope,
                "total": len(items),
                "limit": 50,
            }
        )

    def _prune_update_jobs(self) -> None:
        cutoff = time.time() - 3600
        stale_plan_ids = [
            plan_id
            for plan_id, plan in self._update_plans.items()
            if float(plan.get("updated_at", 0)) < cutoff
        ]
        for plan_id in stale_plan_ids:
            self._update_plans.pop(plan_id, None)
        stale_ids = [
            job_id
            for job_id, job in self._update_jobs.items()
            if float(job.get("updated_at", 0)) < cutoff
            and job.get("status") in {"completed", "failed"}
        ]
        for job_id in stale_ids:
            self._update_jobs.pop(job_id, None)
            self._update_tasks.pop(job_id, None)

    async def start_structured_update_job(
        self, memory_engine, memory_processor
    ) -> dict[str, Any]:
        """Validate and start a tracked structured-memory propagation job."""
        if memory_processor is None:
            return self.utils.error("MemoryProcessor 未初始化")
        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")
        update_mode = str(payload.get("update_mode", "in_place")).strip().lower()
        if update_mode not in {"rebuild", "in_place"}:
            return self.utils.error("update_mode 必须是 rebuild 或 in_place")
        scope = str(payload.get("scope", "current")).strip().lower()
        if scope not in {"current", "session", "persona"}:
            return self.utils.error("scope 必须是 current、session 或 persona")

        source = await self._get_memory_record(memory_id, memory_engine)
        if not source:
            return self.utils.error("记忆不存在")
        source_metadata = self.utils.normalize_metadata(source.get("metadata"))

        value = payload.get("value")
        candidates: list[dict[str, Any]] = []
        if scope == "current":
            if not isinstance(value, dict):
                return self.utils.error("value 必须是结构化记忆对象")
        else:
            plan_id = str(payload.get("plan_id", "")).strip()
            plan = self._update_plans.get(plan_id)
            if not plan:
                return self.utils.error("修改计划不存在或已过期，请重新检测关联记忆")
            if (
                int(plan.get("source_memory_id", -1)) != memory_id
                or plan.get("scope") != scope
            ):
                return self.utils.error("修改计划与当前记忆或作用域不匹配")
            if self._memory_fingerprint(source) != plan.get("source_fingerprint"):
                return self.utils.error("当前记忆在预览后已发生变化，请重新检测")
            value = plan.get("source_value")
            raw_item_ids = payload.get("selected_plan_item_ids", [])
            if not isinstance(raw_item_ids, list):
                return self.utils.error("selected_plan_item_ids 必须是数组")
            selected_ids = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in raw_item_ids
                    if str(item).strip()
                )
            )
            if len(selected_ids) > 50:
                return self.utils.error("单次最多更新 50 条关联记忆")
            if selected_ids and payload.get("risk_acknowledged") is not True:
                return self.utils.error("更新关联记忆前必须确认已了解风险并核对修改内容")
            planned_by_id = {
                str(item.get("plan_item_id")): item
                for item in plan.get("items", [])
            }
            unknown = [
                item_id for item_id in selected_ids if item_id not in planned_by_id
            ]
            if unknown:
                return self.utils.error("包含未审核或无效的关联修改计划项")
            for item_id in selected_ids:
                planned = planned_by_id[item_id]
                candidate_id = int(planned["memory_id"])
                candidate = await self._get_memory_record(candidate_id, memory_engine)
                if not candidate:
                    return self.utils.error(f"关联记忆 {candidate_id} 不存在")
                candidate_metadata = self.utils.normalize_metadata(
                    candidate.get("metadata")
                )
                if not self._scope_matches(source_metadata, candidate_metadata, scope):
                    return self.utils.error(f"关联记忆 {candidate_id} 不在所选范围内")
                if self._memory_fingerprint(candidate) != planned.get(
                    "memory_fingerprint"
                ):
                    return self.utils.error(
                        f"关联记忆 {candidate_id} 在预览后已发生变化，请重新检测"
                    )
                candidates.append(
                    {
                        "memory": candidate,
                        "plan_item_id": item_id,
                        "proposed_value": planned["proposed_value"],
                        "modifications": planned.get("modifications", []),
                        "plan_id": plan_id,
                    }
                )

        if not isinstance(value, dict):
            return self.utils.error("修改计划中的结构化记忆数据无效")
        if not str(value.get("summary", "")).strip():
            return self.utils.error("记忆摘要不能为空")

        self._prune_update_jobs()
        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "total": 1 + len(candidates),
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "percent": 0,
            "current_item": None,
            "results": [],
            "created_at": now,
            "updated_at": now,
        }
        self._update_jobs[job_id] = job
        task = asyncio.create_task(
            self._run_structured_update_job(
                job,
                memory_engine,
                memory_processor,
                source,
                value,
                str(payload.get("reason", "")).strip(),
                update_mode,
                candidates,
                str(payload.get("plan_id", "")).strip() or None,
            ),
            name=f"livingmemory-webui-update-{job_id[:8]}",
        )
        self._update_tasks[job_id] = task
        task.add_done_callback(
            lambda finished, key=job_id: self._finish_update_task(key, finished)
        )
        return self.utils.ok(dict(job))

    def _finish_update_task(self, job_id: str, task: asyncio.Task) -> None:
        """Consume task errors and prevent interrupted jobs from staying active."""
        self._update_tasks.pop(job_id, None)
        job = self._update_jobs.get(job_id)
        if not job or job.get("status") in {"completed", "failed"}:
            return
        if task.cancelled():
            error = "后台更新任务已取消"
        else:
            exception = task.exception()
            if exception is None:
                return
            error = str(exception)
            logger.error(f"[PageAPI] 后台更新任务异常终止: {error}")
        job.update(
            status="failed",
            phase="failed",
            current_item=None,
            error=error,
            updated_at=time.time(),
        )

    async def _run_structured_update_job(
        self,
        job: dict[str, Any],
        memory_engine,
        memory_processor,
        source: dict[str, Any],
        edited_value: dict[str, Any],
        reason: str,
        update_mode: str,
        candidates: list[dict[str, Any]],
        plan_id: str | None = None,
    ) -> None:
        job.update(status="running", phase="updating_current", updated_at=time.time())

        async def record_result(
            old_id: int,
            kind: str,
            response: dict[str, Any] | None = None,
            error: str | None = None,
        ) -> bool:
            ok = bool(response and response.get("status") == "ok" and not error)
            data = response.get("data", {}) if ok and response else {}
            item = {
                "old_memory_id": old_id,
                "new_memory_id": data.get("new_memory_id") if ok else None,
                "kind": kind,
                "status": "completed" if ok else "failed",
                "error": error or (response or {}).get("message"),
                "plan_id": data.get("plan_id") if ok else None,
                "plan_item_id": data.get("plan_item_id") if ok else None,
                "modifications": data.get("modifications", []) if ok else [],
            }
            job["results"].append(item)
            job["completed"] += 1
            job["succeeded" if ok else "failed"] += 1
            job["percent"] = round(job["completed"] * 100 / job["total"])
            job["updated_at"] = time.time()
            return ok

        source_id = int(source["id"])
        job["current_item"] = {
            "memory_id": source_id,
            "excerpt": str(source.get("text", ""))[:160],
        }
        try:
            response = await self._replace_structured_memory(
                memory_engine,
                memory_processor,
                source,
                edited_value,
                reason,
                update_mode,
            )
            source_ok = await record_result(source_id, "current", response=response)
        except Exception as exc:
            logger.error("[PageAPI] 更新当前记忆任务失败", exc_info=True)
            source_ok = await record_result(source_id, "current", error=str(exc))

        if not source_ok:
            job.update(
                status="failed",
                phase="failed",
                current_item=None,
                updated_at=time.time(),
            )
            return

        for planned_candidate in candidates:
            candidate = planned_candidate["memory"]
            candidate_id = int(candidate["id"])
            job.update(
                phase="updating_related",
                current_item={
                    "memory_id": candidate_id,
                    "excerpt": str(candidate.get("text", ""))[:160],
                },
                updated_at=time.time(),
            )
            try:
                response = await self._replace_structured_memory(
                    memory_engine,
                    memory_processor,
                    candidate,
                    planned_candidate["proposed_value"],
                    reason or f"根据记忆 {source_id} 的人工修订同步校正",
                    update_mode,
                )
                if response.get("status") == "ok":
                    response.setdefault("data", {})["plan_id"] = plan_id
                    response["data"]["plan_item_id"] = planned_candidate[
                        "plan_item_id"
                    ]
                    response["data"]["modifications"] = planned_candidate.get(
                        "modifications", []
                    )
                await record_result(candidate_id, "related", response=response)
            except Exception as exc:
                logger.error(
                    f"[PageAPI] 校正关联记忆 {candidate_id} 失败", exc_info=True
                )
                await record_result(candidate_id, "related", error=str(exc))

        job.update(
            status="completed",
            phase="completed",
            current_item=None,
            percent=100,
            updated_at=time.time(),
        )

    async def get_structured_update_progress(self) -> dict[str, Any]:
        job_id = str(request.args.get("job_id", "")).strip()
        if not job_id:
            return self.utils.error("需要提供 job_id")
        job = self._update_jobs.get(job_id)
        if not job:
            return self.utils.error("更新任务不存在或已过期")
        return self.utils.ok(dict(job))

    async def update_memory(self, memory_engine, memory_processor=None) -> dict[str, Any]:
        """
        更新单个记忆的字段

        支持的字段:
            - structured: 摘要、主题、事实、参与者等结构化字段（整体重建索引）
            - content: 记忆内容（会创建新记忆并删除旧记忆）
            - importance: 重要性（0-1 或 0-10）
            - status: 状态（active/archived/deleted）
            - type: 类型

        Payload:
            - memory_id: 记忆ID（必需）
            - field: 要更新的字段（必需）
            - value: 新值（必需）
            - reason: 更新原因（可选）
            - update_mode: rebuild 或 in_place（仅 structured，默认 rebuild）

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
        update_mode = str(payload.get("update_mode", "rebuild")).strip().lower()

        if not field or value is None:
            return self.utils.error("需要指定 field 和 value")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")

        current_metadata = self.utils.normalize_metadata(memory.get("metadata"))

        if field == "structured":
            if not isinstance(value, dict):
                return self.utils.error("structured 字段必须是对象")
            return await self._replace_structured_memory(
                memory_engine,
                memory_processor,
                memory,
                value,
                reason,
                update_mode,
            )

        # 特殊处理：content 更新需要重新创建记忆
        if field == "content":
            new_content = str(value).strip()
            if not new_content:
                return self.utils.error("记忆内容不能为空")

            # Legacy callers are made safe by clearing stale structured fields.
            return await self._replace_structured_memory(
                memory_engine,
                memory_processor,
                memory,
                {
                    "summary": new_content,
                    "persona_summary": new_content,
                    "topics": [],
                    "key_facts": [],
                    "participants": current_metadata.get("participants", []),
                    "sentiment": current_metadata.get("sentiment", "neutral"),
                    "importance": clamp_float(
                        current_metadata.get("importance"), default=0.5
                    ),
                    "importance_scale": "stored",
                    "memory_type": current_metadata.get("memory_type", "GENERAL"),
                    "status": current_metadata.get("status", "active"),
                },
                reason,
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
