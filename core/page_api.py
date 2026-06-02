"""
官方插件 Page API 适配层。

职责：
1. 为 AstrBot 官方插件页面注册原生 Web API。
2. 直接复用插件运行期组件，不再代理到旧 FastAPI WebUI。
3. 保留返回结构与旧前端尽量一致，降低页面迁移成本。
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from quart import request

from astrbot.api import logger

from .managers.backup_manager import BackupManager
from .utils.number_utils import clamp_float, safe_float

PLUGIN_NAME = "astrbot_plugin_livingmemory"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """LivingMemory 官方插件页面 API。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        """注册官方插件页面所需的原生 API。"""
        register = self.plugin.context.register_web_api
        register(
            f"{PAGE_API_PREFIX}/stats",
            self.get_stats,
            ["GET"],
            "LivingMemory Page stats",
        )
        register(
            f"{PAGE_API_PREFIX}/memories",
            self.list_memories,
            ["GET"],
            "LivingMemory Page memories",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/detail",
            self.get_memory_detail,
            ["GET"],
            "LivingMemory Page memory detail",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/update",
            self.update_memory,
            ["POST"],
            "LivingMemory Page update memory",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-delete",
            self.batch_delete_memories,
            ["POST"],
            "LivingMemory Page batch delete memories",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-update",
            self.batch_update_memories,
            ["POST"],
            "LivingMemory Page batch update memories",
        )
        register(
            f"{PAGE_API_PREFIX}/recall/test",
            self.test_recall,
            ["POST"],
            "LivingMemory Page recall test",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/overview",
            self.get_graph_overview,
            ["GET"],
            "LivingMemory Page graph overview",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/query",
            self.query_graph,
            ["POST"],
            "LivingMemory Page graph query",
        )
        register(
            f"{PAGE_API_PREFIX}/backups",
            self.list_backups,
            ["GET"],
            "LivingMemory Page backup list",
        )

    async def get_stats(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        try:
            stats = await memory_engine.get_statistics()

            # Use dedicated COUNT(*) stats so the dashboard shows full graph totals.
            graph_store = self._get_graph_store(memory_engine)
            if graph_store is not None:
                try:
                    entry_stats = await graph_store.get_memory_entry_stats()
                    stats["graph_nodes"] = entry_stats.get("graph_nodes", 0)
                    stats["graph_edges"] = entry_stats.get("graph_edges", 0)
                    stats["graph_entries"] = entry_stats.get("graph_entries", 0)
                except Exception:
                    stats["graph_nodes"] = 0
                    stats["graph_edges"] = 0
                    stats["graph_entries"] = 0
            else:
                stats["graph_nodes"] = 0
                stats["graph_edges"] = 0
                stats["graph_entries"] = 0

            # 原子统计 (if available)
            atom_store = getattr(memory_engine, "atom_store", None)
            stats["atom_count"] = 0
            stats["atom_breakdown"] = {}
            if atom_store is not None:
                try:
                    stats["atom_count"] = await atom_store.count_atoms() or 0
                except Exception:
                    pass

            # 重要性分布 — 默认零值，前端正常展示
            if "importance_distribution" not in stats:
                stats["importance_distribution"] = {
                    "0-1": 0,
                    "1-2": 0,
                    "2-3": 0,
                    "3-4": 0,
                    "4-5": 0,
                    "5-6": 0,
                    "6-7": 0,
                    "7-8": 0,
                    "8-9": 0,
                    "9-10": 0,
                }

            # 最近会话从 sessions 统计数据派生
            session_data = stats.get("sessions", {})
            stats["recent_sessions"] = (
                [
                    {"session_id": sid, "message_count": cnt}
                    for sid, cnt in sorted(session_data.items(), key=lambda x: -x[1])[
                        :10
                    ]
                ]
                if isinstance(session_data, dict)
                else []
            )

            return self._ok(stats)
        except Exception as exc:
            logger.error(f"[PageAPI] 获取统计信息失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def list_memories(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        query = request.args
        session_id = str(query.get("session_id", "")).strip() or None
        keyword = str(query.get("keyword", "")).strip()
        status_filter = str(query.get("status", "all")).strip().lower() or "all"

        try:
            page = max(1, int(query.get("page", 1)))
            page_size = min(500, max(1, int(query.get("page_size", 20))))
        except (TypeError, ValueError):
            return self._error("分页参数无效")

        sort_field_raw = query.get("sort")
        sort_field = str(sort_field_raw).strip().lower() if sort_field_raw else None
        sort_order = str(query.get("order", "desc")).strip().lower()

        ALLOWED_SORT_FIELDS = {"id", "created_at", "importance"}
        ALLOWED_SORT_ORDERS = {"asc", "desc"}

        if sort_field not in ALLOWED_SORT_FIELDS:
            sort_field = None
        if sort_order not in ALLOWED_SORT_ORDERS:
            sort_order = "desc"

        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            return self._error("MemoryEngine db_path unavailable")

        offset = (page - 1) * page_size
        where_clauses: list[str] = []
        params: list[Any] = []

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

        if sort_field == "importance":
            sort_expr = (
                "COALESCE("
                "CASE WHEN json_valid(metadata) "
                "THEN CAST(json_extract(metadata, '$.importance') AS REAL) END,"
                "5)"
            )
        elif sort_field == "id":
            sort_expr = "CAST(id AS REAL)"
        elif sort_field == "created_at":
            sort_expr = (
                "COALESCE("
                "CASE WHEN json_valid(metadata) "
                "THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,"
                "0)"
            )
        else:
            sort_expr = (
                "COALESCE("
                "CASE WHEN json_valid(metadata) "
                "THEN CAST(json_extract(metadata, '$.create_time') AS REAL) END,"
                "0)"
            )
            sort_order = "desc"

        order_sql = "ASC" if sort_order == "asc" else "DESC"

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
                    ORDER BY {sort_expr} {order_sql}, id {order_sql}
                    LIMIT ? OFFSET ?
                    """,
                    (*params, page_size, offset),
                )
                rows = await cursor.fetchall()
        except Exception as exc:
            logger.error(f"[PageAPI] 获取记忆列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "doc_id": row["doc_id"],
                    "text": row["text"],
                    "metadata": self._normalize_metadata(row["metadata"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return self._ok(
            {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": (offset + page_size) < total,
            }
        )

    async def get_memory_detail(self):
        """Return the full detail payload for a single memory."""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error

        query = request.args
        try:
            memory_id = int(query.get("memory_id", ""))
        except (TypeError, ValueError):
            return self._error("memory_id 必须是整数")

        memory = await self._get_memory_record(memory_id)
        if not memory:
            return self._error("记忆不存在")

        metadata = self._normalize_metadata(memory.get("metadata"))

        # Build a full detail payload for the drawer.
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
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "update_history": metadata.get("update_history", []),
        }

        # Attach a small related graph when graph storage is available.
        graph_store = self._get_graph_store(ready["memory_engine"])
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

        return self._ok(detail)

    async def update_memory(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self._error("memory_id 必须是整数")

        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        reason = str(payload.get("reason", "")).strip()

        if not field or value is None:
            return self._error("需要指定 field 和 value")

        memory = await self._get_memory_record(memory_id)
        if not memory:
            return self._error("记忆不存在")

        current_metadata = self._normalize_metadata(memory.get("metadata"))

        if field == "content":
            new_content = str(value).strip()
            if not new_content:
                return self._error("记忆内容不能为空")

            session_id = current_metadata.get("session_id")
            persona_id = current_metadata.get("persona_id")
            importance = clamp_float(current_metadata.get("importance"), default=0.5)
            updated_at = time.time()
            update_history = self._append_update_history(
                current_metadata,
                field="content",
                old_value=memory.get("text", ""),
                new_value=new_content,
                reason=reason,
                timestamp=updated_at,
            )

            if reason:
                current_metadata["update_reason"] = reason
            current_metadata["updated_at"] = updated_at
            current_metadata["previous_content"] = str(memory.get("text", ""))[:100]
            current_metadata["update_history"] = update_history

            new_memory_id = None
            try:
                new_memory_id = await memory_engine.add_memory(
                    content=new_content,
                    session_id=session_id,
                    persona_id=persona_id,
                    importance=importance,
                    metadata=current_metadata,
                )
                delete_success = await memory_engine.delete_memory(memory_id)
                if not delete_success:
                    await memory_engine.delete_memory(new_memory_id)
                    return self._error("旧记忆删除失败，已回滚本次内容更新")
            except Exception as exc:
                if new_memory_id is not None:
                    try:
                        await memory_engine.delete_memory(new_memory_id)
                    except Exception:
                        logger.error(
                            f"[PageAPI] 回滚新记忆失败 (new_memory_id={new_memory_id})",
                            exc_info=True,
                        )
                logger.error(f"[PageAPI] 更新记忆内容失败: {exc}", exc_info=True)
                return self._error(str(exc))

            return {
                "status": "ok",
                "data": {
                    "message": f"记忆内容已更新（ID: {memory_id} → {new_memory_id}）",
                    "old_memory_id": memory_id,
                    "new_memory_id": new_memory_id,
                    "field": field,
                },
            }

        updates: dict[str, Any] = {}
        old_value_for_history: Any
        new_value_for_history: Any
        if field == "importance":
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return self._error("重要性必须是数字")
            if 0.0 <= parsed <= 1.0:
                normalized = parsed
            elif 0.0 <= parsed <= 10.0:
                normalized = parsed / 10.0
            else:
                return self._error("重要性必须在 0-1 或 0-10 范围内")
            updates["importance"] = normalized
            old_value_for_history = self._importance_to_display(
                current_metadata.get("importance", 0.5)
            )
            new_value_for_history = round(normalized * 10.0, 2)
        elif field == "status":
            status_value = str(value).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self._error("状态必须是 active、archived 或 deleted")
            updates["metadata"] = {"status": status_value}
            old_value_for_history = current_metadata.get("status", "active")
            new_value_for_history = status_value
        elif field == "type":
            type_value = str(value).strip()
            if not type_value:
                return self._error("类型不能为空")
            updates["metadata"] = {"memory_type": type_value}
            old_value_for_history = current_metadata.get("memory_type", "GENERAL")
            new_value_for_history = type_value
        else:
            return self._error(f"不支持编辑字段: {field}")

        updated_at = time.time()
        updates.setdefault("metadata", {})
        updates["metadata"]["update_history"] = self._append_update_history(
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
            return self._error(str(exc))

        if not success:
            return self._error("更新失败")

        return {
            "status": "ok",
            "data": {
                "message": f"记忆 {memory_id} 的 {field} 已更新",
                "memory_id": memory_id,
                "field": field,
            },
        }

    async def batch_delete_memories(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        if not isinstance(memory_ids, list) or not memory_ids:
            return self._error("需要提供记忆 ID 列表")

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

        return self._ok(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_update_memories(self):
        """Batch update editable memory fields."""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")

        if not isinstance(memory_ids, list) or not memory_ids:
            return self._error("需要提供记忆 ID 列表")
        if not field or value is None:
            return self._error("需要指定 field 和 value")

        if field not in ("status", "importance", "type"):
            return self._error(f"批量更新不支持字段: {field}")

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
                        parsed = float(value)
                    except (TypeError, ValueError):
                        failed_ids.append(raw_id)
                        continue
                    if 0.0 <= parsed <= 1.0:
                        updates["importance"] = parsed
                    elif 0.0 <= parsed <= 10.0:
                        updates["importance"] = parsed / 10.0
                    else:
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

        return self._ok(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )

    async def test_recall(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        query_text = str(payload.get("query", "")).strip()
        if not query_text:
            return self._error("查询内容不能为空")

        try:
            k = min(50, max(1, int(payload.get("k", 5))))
        except (TypeError, ValueError):
            return self._error("k 必须是整数")

        session_id = payload.get("session_id")

        try:
            start_time = time.time()
            results = await memory_engine.search_memories(
                query=query_text,
                k=k,
                session_id=session_id,
                persona_id=None,
            )
            elapsed_time = (time.time() - start_time) * 1000
        except Exception as exc:
            logger.error(f"[PageAPI] 召回测试失败: {exc}", exc_info=True)
            return self._error(str(exc))

        formatted_results = []
        for result in results:
            score_breakdown = {
                key: round(float(value), 6)
                for key, value in (
                    getattr(result, "score_breakdown", None) or {}
                ).items()
                if isinstance(value, (int, float))
            }
            metadata = {
                "session_id": result.metadata.get("session_id"),
                "persona_id": result.metadata.get("persona_id"),
                "importance": result.metadata.get("importance", 0.5),
                "memory_type": result.metadata.get("memory_type", "GENERAL"),
                "status": result.metadata.get("status", "active"),
                "create_time": result.metadata.get("create_time"),
            }
            metadata.update(score_breakdown)
            formatted_results.append(
                {
                    "memory_id": result.doc_id,
                    "content": result.content,
                    "similarity_score": round(float(result.final_score), 4),
                    "score_percentage": round(float(result.final_score) * 100, 2),
                    "metadata": metadata,
                    "score_breakdown": score_breakdown,
                }
            )

        return self._ok(
            {
                "results": formatted_results,
                "total": len(formatted_results),
                "query": query_text,
                "k": k,
                "session_id_filter": session_id,
                "elapsed_time_ms": round(elapsed_time, 2),
            }
        )

    async def get_graph_overview(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        args = request.args
        session_id = str(args.get("session_id", "")).strip() or None
        persona_id = str(args.get("persona_id", "")).strip() or None

        try:
            limit_memories = max(1, min(int(args.get("limit_memories", 12)), 24))
            limit_entries = max(12, min(int(args.get("limit_entries", 36)), 80))
            limit_nodes = max(12, min(int(args.get("limit_nodes", 48)), 80))
            limit_edges = max(12, min(int(args.get("limit_edges", 72)), 120))
        except (TypeError, ValueError):
            return self._error("图谱分页参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self._get_graph_store(memory_engine)
            empty_snapshot = {
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
            if graph_store is None:
                return self._ok(
                    self._build_graph_view_payload(
                        empty_snapshot,
                        stats,
                        enabled=False,
                        mode="overview",
                        filters={
                            "session_id": session_id,
                            "persona_id": persona_id,
                        },
                    )
                )

            snapshot = await graph_store.get_graph_snapshot(
                session_id=session_id,
                persona_id=persona_id,
                limit_memories=limit_memories,
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
            return self._ok(
                self._build_graph_view_payload(
                    snapshot,
                    stats,
                    enabled=True,
                    mode="overview",
                    filters={
                        "session_id": session_id,
                        "persona_id": persona_id,
                    },
                )
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 获取图谱概览失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def query_graph(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        query_text = str(payload.get("query", "")).strip()
        session_id = str(payload.get("session_id", "")).strip() or None
        persona_id = str(payload.get("persona_id", "")).strip() or None
        memory_id_raw = payload.get("memory_id")

        try:
            limit_memories = max(1, min(int(payload.get("limit_memories", 10)), 24))
            limit_entries = max(12, min(int(payload.get("limit_entries", 40)), 80))
            limit_nodes = max(12, min(int(payload.get("limit_nodes", 56)), 80))
            limit_edges = max(12, min(int(payload.get("limit_edges", 96)), 120))
        except (TypeError, ValueError):
            return self._error("图谱检索参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self._get_graph_store(memory_engine)
            empty_snapshot = {
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
            if graph_store is None:
                return self._ok(
                    self._build_graph_view_payload(
                        empty_snapshot,
                        stats,
                        enabled=False,
                        mode="query",
                        query=query_text,
                        filters={
                            "session_id": session_id,
                            "persona_id": persona_id,
                        },
                    )
                )

            if memory_id_raw not in (None, ""):
                try:
                    memory_id = int(memory_id_raw)
                except (TypeError, ValueError):
                    return self._error("memory_id 必须是整数")

                snapshot = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self._ok(
                    self._build_graph_view_payload(
                        snapshot,
                        stats,
                        enabled=True,
                        mode="memory_focus",
                        memory_id=memory_id,
                        filters={
                            "session_id": session_id,
                            "persona_id": persona_id,
                        },
                    )
                )

            if not query_text:
                snapshot = await graph_store.get_graph_snapshot(
                    session_id=session_id,
                    persona_id=persona_id,
                    limit_memories=limit_memories,
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self._ok(
                    self._build_graph_view_payload(
                        snapshot,
                        stats,
                        enabled=True,
                        mode="overview",
                        filters={
                            "session_id": session_id,
                            "persona_id": persona_id,
                        },
                    )
                )

            search_results = await memory_engine.search_memories(
                query=query_text,
                k=limit_memories,
                session_id=session_id,
                persona_id=persona_id,
            )
            retrieval_items = []
            matched_memory_ids: list[int] = []
            seen_memory_ids: set[int] = set()
            for result in search_results:
                memory_id = int(result.doc_id)
                if memory_id not in seen_memory_ids:
                    seen_memory_ids.add(memory_id)
                    matched_memory_ids.append(memory_id)
                retrieval_items.append(
                    {
                        "memory_id": memory_id,
                        "content": result.content,
                        "metadata": result.metadata,
                        "final_score": round(float(result.final_score), 6),
                        "rrf_score": round(float(result.rrf_score), 6),
                        "bm25_score": (
                            round(float(result.bm25_score), 6)
                            if result.bm25_score is not None
                            else None
                        ),
                        "vector_score": (
                            round(float(result.vector_score), 6)
                            if result.vector_score is not None
                            else None
                        ),
                        "score_breakdown": {
                            key: round(float(value), 6)
                            for key, value in (result.score_breakdown or {}).items()
                            if isinstance(value, (int, float))
                        },
                    }
                )

            tokens = self._tokenize_graph_query(query_text)
            matched_node_ids: list[int] = []
            if tokens:
                node_hits = await graph_store.search_nodes_by_tokens(
                    tokens,
                    limit=max(8, min(limit_nodes, 24)),
                )
                matched_node_ids = [int(item["id"]) for item in node_hits]

                node_entry_hits = await graph_store.get_entries_for_node_ids(
                    matched_node_ids,
                    limit=max(8, min(limit_entries, 24)),
                    session_id=session_id,
                    persona_id=persona_id,
                )
                for hit in node_entry_hits:
                    memory_id = int(hit["source_memory_id"])
                    if memory_id not in seen_memory_ids:
                        seen_memory_ids.add(memory_id)
                        matched_memory_ids.append(memory_id)

            snapshot = await graph_store.get_subgraph_for_memories(
                matched_memory_ids[:limit_memories],
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
            return self._ok(
                self._build_graph_view_payload(
                    snapshot,
                    stats,
                    enabled=True,
                    mode="query",
                    query=query_text,
                    retrieval_items=retrieval_items,
                    matched_node_ids=matched_node_ids,
                    filters={
                        "session_id": session_id,
                        "persona_id": persona_id,
                    },
                )
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 图谱查询失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def _ensure_plugin_ready(self) -> tuple[dict[str, Any] | None, dict | None]:
        ready, message = await self.plugin._ensure_plugin_ready()
        if not ready:
            return None, self._error(message or "插件尚未就绪")

        memory_engine = self.plugin.initializer.memory_engine
        if memory_engine is None:
            return None, self._error("记忆引擎未初始化")

        return {
            "memory_engine": memory_engine,
            "conversation_manager": self.plugin.initializer.conversation_manager,
            "index_validator": self.plugin.initializer.index_validator,
        }, None

    async def _get_memory_record(self, memory_id: int) -> dict[str, Any] | None:
        memory_engine = self.plugin.initializer.memory_engine
        if memory_engine is None:
            return None

        memory = await memory_engine.get_memory(memory_id)
        if memory:
            return memory

        if memory_engine.db_connection is None:
            return None

        try:
            cursor = await memory_engine.db_connection.execute(
                "SELECT id, text, metadata FROM documents WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
        except Exception:
            return None

        if not row:
            return None

        return {
            "id": row[0],
            "text": row[1],
            "metadata": self._normalize_metadata(row[2]),
        }

    @staticmethod
    def _normalize_metadata(metadata: Any) -> dict[str, Any]:
        if isinstance(metadata, dict):
            return metadata
        if not metadata:
            return {}
        try:
            parsed = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _importance_to_display(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = 0.5
        if parsed <= 1.0:
            parsed *= 10.0
        return round(max(0.0, min(10.0, parsed)), 2)

    @classmethod
    def _append_update_history(
        cls,
        metadata: dict[str, Any],
        *,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        timestamp: float,
    ) -> list[dict[str, Any]]:
        raw_history = metadata.get("update_history", [])
        history = raw_history if isinstance(raw_history, list) else []
        next_history = [item for item in history[-19:] if isinstance(item, dict)]
        next_history.append(
            {
                "timestamp": timestamp,
                "field": field,
                "old_value": cls._history_value(old_value),
                "new_value": cls._history_value(new_value),
                "reason": reason,
                "description": cls._history_description(
                    field, old_value, new_value, reason
                ),
            }
        )
        return next_history

    @staticmethod
    def _history_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _history_description(
        cls,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
    ) -> str:
        old_text = cls._short_history_text(old_value)
        new_text = cls._short_history_text(new_value)
        suffix = f" ({reason})" if reason else ""
        return f"{field}: {old_text} → {new_text}{suffix}"

    @staticmethod
    def _short_history_text(value: Any) -> str:
        text = str(value if value is not None else "")
        text = " ".join(text.split())
        return text if len(text) <= 64 else f"{text[:61]}..."

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return {"status": "ok", "data": data}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"status": "error", "message": str(message)}

    @staticmethod
    def _get_graph_store(memory_engine):
        return getattr(memory_engine, "graph_store", None)

    @staticmethod
    def _tokenize_graph_query(query: str) -> list[str]:
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []

        normalized = "".join(
            character if character.isalnum() else " " for character in query_text
        )
        raw_tokens = [token for token in normalized.split() if token]
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(value: str):
            token = value.strip()
            if len(token) < 2 or token in seen:
                return
            seen.add(token)
            tokens.append(token)

        for token in raw_tokens:
            add_token(token)

        compact = "".join(character for character in query_text if character.isalnum())
        if compact and any(ord(character) > 127 for character in compact):
            add_token(compact)
            for size in (2, 3):
                if len(tokens) >= 12:
                    break
                max_index = max(0, len(compact) - size + 1)
                for index in range(max_index):
                    add_token(compact[index : index + size])
                    if len(tokens) >= 12:
                        break

        return tokens[:12]

    @staticmethod
    def _build_graph_view_payload(
        snapshot: dict[str, Any],
        stats: dict[str, Any],
        *,
        enabled: bool,
        mode: str,
        query: str | None = None,
        memory_id: int | None = None,
        retrieval_items: list[dict[str, Any]] | None = None,
        matched_node_ids: list[int] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nodes = [dict(item) for item in snapshot.get("nodes", [])]
        edges = [dict(item) for item in snapshot.get("edges", [])]
        entries = [dict(item) for item in snapshot.get("entries", [])]
        memories = [dict(item) for item in snapshot.get("memories", [])]
        retrieval_items = [dict(item) for item in (retrieval_items or [])]
        matched_node_ids = [int(item) for item in (matched_node_ids or [])]
        matched_node_id_set = set(matched_node_ids)
        retrieval_lookup = {
            int(item["memory_id"]): item
            for item in retrieval_items
            if item.get("memory_id") is not None
        }

        node_type_breakdown: dict[str, int] = {}
        relation_breakdown: dict[str, int] = {}

        for node in nodes:
            node["highlighted"] = int(node.get("id", 0)) in matched_node_id_set
            node_type = str(node.get("type", "unknown") or "unknown")
            node_type_breakdown[node_type] = node_type_breakdown.get(node_type, 0) + 1

        for edge in edges:
            relation_type = str(edge.get("relation_type", "related") or "related")
            relation_breakdown[relation_type] = (
                relation_breakdown.get(relation_type, 0) + 1
            )

        for memory in memories:
            memory_key = memory.get("memory_id")
            if memory_key is None:
                continue
            retrieval = retrieval_lookup.get(int(memory_key))
            if retrieval is not None:
                memory["retrieval"] = retrieval

        top_nodes = sorted(
            nodes,
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -int(item.get("degree", 0)),
                str(item.get("label", "")),
            ),
        )[:8]
        top_memories = sorted(
            memories,
            key=lambda item: (
                -safe_float((item.get("retrieval") or {}).get("final_score"), -1.0),
                -int(item.get("entry_count", 0)),
                -int(item.get("node_count", 0)),
                -int(item.get("edge_count", 0)),
                -safe_float(item.get("importance"), 0.0),
            ),
        )[:8]

        summary = {
            "visible_node_count": len(nodes),
            "visible_edge_count": len(edges),
            "visible_entry_count": len(entries),
            "visible_memory_count": len(memories),
            "graph_node_count": int(stats.get("graph_nodes", 0) or 0),
            "graph_edge_count": int(stats.get("graph_edges", 0) or 0),
            "graph_entry_count": int(stats.get("graph_entries", 0) or 0),
            "graph_memory_enabled": bool(enabled),
            "node_type_breakdown": node_type_breakdown,
            "relation_breakdown": relation_breakdown,
        }

        return {
            "enabled": enabled,
            "mode": mode,
            "query": query or None,
            "memory_id": memory_id,
            "filters": filters or {},
            "summary": summary,
            "matched_node_ids": matched_node_ids,
            "matched_memory_ids": [item["memory_id"] for item in retrieval_items],
            "top_nodes": top_nodes,
            "top_memories": top_memories,
            "retrieval": {
                "total": len(retrieval_items),
                "items": retrieval_items,
            },
            "snapshot": {
                "nodes": nodes,
                "edges": edges,
                "entries": entries,
                "memories": memories,
            },
        }

    async def list_backups(self):
        """列出所有版本备份及其元数据。"""
        data_dir = self.plugin.initializer.data_dir if self.plugin.initializer else ""
        if not data_dir:
            return self._ok({"backups": [], "total": 0})
        backups = BackupManager.list_backups(data_dir)
        return self._ok({"backups": backups, "total": len(backups)})
