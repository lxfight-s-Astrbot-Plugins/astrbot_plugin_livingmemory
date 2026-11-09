"""
命令处理器
负责处理插件命令
"""

import os
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .base.config_manager import ConfigManager
from .base.usage_metrics import get_usage_snapshot
from .command_handler_external import ExternalApiCommandMixin
from .i18n_backend import t, t_list
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .managers.scope_migrator import ScopeMigrator
from .memory_scope import (
    GLOBAL_MEMORY_SCOPE,
    is_event_memory_allowed,
    resolve_event_memory_access,
    resolve_memory_scope,
    resolve_user_memory_scope,
)
from .memory_source import serialize_source_messages
from .retrieval.recall_trace import trace_request_context
from .validators.index_maintenance import IndexValidator


class CommandHandler(ExternalApiCommandMixin):
    """命令处理器"""

    def __init__(
        self,
        context,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine | None,
        conversation_manager: ConversationManager | None,
        index_validator: IndexValidator | None,
        memory_processor=None,
        initialization_status_callback=None,
    ):
        """
        初始化命令处理器

        Args:
            context: AstrBot Context
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            index_validator: 索引验证器
            memory_processor: 记忆处理器（用于手动总结）
            initialization_status_callback: 初始化状态回调函数
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self._memory_processor = memory_processor
        self.get_initialization_status = initialization_status_callback

    @staticmethod
    def _format_error_message(
        action: str, error: Exception, suggestions: list[str] | None = None
    ) -> str:
        """Format user-facing error message with actionable hints."""
        message = [
            t("error.format.action_failed", action=action),
            t("error.format.details", error=error),
        ]
        if suggestions:
            message.append("")
            message.append(t("error.format.suggestions"))
            for index, suggestion in enumerate(suggestions, start=1):
                message.append(
                    t(
                        "error.format.suggestion_item",
                        index=index,
                        suggestion=suggestion,
                    )
                )
        return "\n".join(message)

    async def handle_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem status 命令"""
        try:
            stats = await self.memory_engine.get_statistics()

            # 格式化时间
            last_update = t("common.never")
            if stats.get("newest_memory"):
                last_update = datetime.fromtimestamp(stats["newest_memory"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            # 计算数据库大小
            db_size = 0.0
            if os.path.exists(self.memory_engine.db_path):
                db_size = os.path.getsize(self.memory_engine.db_path) / (1024 * 1024)

            session_count = len(stats.get("sessions", {}))

            message = t(
                "status.report",
                total=stats["total_memories"],
                session_count=session_count,
                last_update=last_update,
                db_size=db_size,
            )

            usage = get_usage_snapshot()
            message += t(
                "status.usage",
                calls=usage["calls"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                fallback_calls=usage.get("fallback_calls", 0),
            )

            maintenance = stats.get("index_maintenance") or {}
            maintenance_state = str(maintenance.get("state") or "idle")
            if maintenance_state not in {"idle", "ready"}:
                message += t(
                    "status.index_maintenance",
                    state=maintenance_state,
                    current=int(maintenance.get("current", 0) or 0),
                    total=int(maintenance.get("total", 0) or 0),
                    message=str(maintenance.get("message") or ""),
                )

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("status.action_name"),
                    e,
                    t_list("error.suggestions.status"),
                )
            )

    @staticmethod
    def _format_size_mb(value: int | None) -> str:
        """把字节数格式化为两位小数 MB；None 表示未知。"""
        if value is None:
            return t("common.unknown")
        return f"{value / (1024 * 1024):.2f}"

    @staticmethod
    def _format_timestamp(value: int | float | None) -> str:
        """把 epoch 秒格式化为本地时间；缺失/非法值显示 unknown。"""
        if not isinstance(value, (int, float)) or value <= 0:
            return t("common.unknown")
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

    def _format_diag_report(self, diagnostics: dict) -> str:
        """把诊断字典渲染成多行文本（缺失字段显示 unknown）。"""
        unknown = t("common.unknown")
        documents = diagnostics.get("documents")
        if isinstance(documents, dict):
            doc_values = {
                key: documents.get(key, unknown)
                for key in ("total", "active", "archived", "deleted")
            }
        else:
            doc_values = dict.fromkeys(
                ("total", "active", "archived", "deleted"), unknown
            )

        graph = diagnostics.get("graph")
        graph_nodes = (
            graph.get("nodes", unknown) if isinstance(graph, dict) else unknown
        )
        graph_edges = (
            graph.get("edges", unknown) if isinstance(graph, dict) else unknown
        )

        faiss = diagnostics.get("faiss") or {}
        faiss_main = faiss.get("main") if faiss.get("main") is not None else unknown
        faiss_graph = faiss.get("graph") if faiss.get("graph") is not None else unknown

        migration = diagnostics.get("migration") or {}
        pending = migration.get("pending") or []
        pending_text = (
            ", ".join(str(item) for item in pending) if pending else t("common.none")
        )
        pending_documents = migration.get("pending_documents")
        if pending_documents is None:
            pending_documents = unknown
        legacy_markers = migration.get("legacy_session_markers", unknown)

        backups = diagnostics.get("backups") or {}
        backup_count = backups.get("count", unknown)
        backup_size = self._format_size_mb(backups.get("bytes"))
        backup_latest = self._format_timestamp(backups.get("latest_at"))

        last_decay = diagnostics.get("last_decay") or {}
        last_decay_text = last_decay.get("date") or self._format_timestamp(
            last_decay.get("timestamp")
        )

        db_version = diagnostics.get("db_version")
        if db_version is None:
            db_version = unknown

        return "\n".join(
            [
                t("diag.header"),
                t("diag.db_version", version=db_version),
                t("diag.quick_check", result=diagnostics.get("quick_check") or unknown),
                t(
                    "diag.migration",
                    pending=pending_text,
                    pending_documents=pending_documents,
                    legacy_markers=legacy_markers,
                ),
                t("diag.documents", **doc_values),
                t("diag.graph", nodes=graph_nodes, edges=graph_edges),
                t("diag.faiss", main=faiss_main, graph=faiss_graph),
                t(
                    "diag.storage",
                    db_size=self._format_size_mb(diagnostics.get("db_bytes")),
                    wal_size=self._format_size_mb(diagnostics.get("wal_bytes")),
                    data_dir=self._format_size_mb(diagnostics.get("data_dir_bytes")),
                ),
                t(
                    "diag.backups",
                    count=backup_count,
                    size=backup_size,
                    latest=backup_latest,
                ),
                t("diag.last_decay", last_decay=last_decay_text),
                t("diag.hint"),
            ]
        )

    async def handle_diag(
        self, event: AstrMessageEvent, mode: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem diag 命令：数据体检；``compact`` 模式执行 VACUUM 压缩。"""
        normalized = (mode or "").strip().casefold()
        try:
            if normalized in {"compact", "vacuum"}:
                yield event.plain_result(t("diag.compact.starting"))
                result = await self.memory_engine.compact_storage()
                yield event.plain_result(
                    t(
                        "diag.compact.done",
                        db_before=self._format_size_mb(result.get("db_size_before")),
                        db_after=self._format_size_mb(result.get("db_size_after")),
                        wal_before=self._format_size_mb(result.get("wal_size_before")),
                        wal_after=self._format_size_mb(result.get("wal_size_after")),
                        reclaimed=self._format_size_mb(result.get("bytes_reclaimed")),
                    )
                )
                return

            yield event.plain_result(t("diag.collecting"))
            diagnostics = await self.memory_engine.get_diagnostics()
            yield event.plain_result(self._format_diag_report(diagnostics))
        except Exception as e:
            logger.error(f"数据体检失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("diag.action_name"),
                    e,
                    t_list("error.suggestions.diag"),
                )
            )

    async def handle_trace(
        self, event: AstrMessageEvent, arg: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem trace 命令：查看最近召回追踪或单条追踪详情。"""
        recorder = getattr(self.memory_engine, "trace_recorder", None)
        if recorder is None or not recorder.enabled:
            yield event.plain_result(t("trace.disabled"))
            return

        argument = (arg or "").strip()
        try:
            if not argument:
                yield event.plain_result(self._format_trace_list(recorder, 5))
                return
            if argument.isdigit():
                count = max(1, min(int(argument), 50))
                yield event.plain_result(self._format_trace_list(recorder, count))
                return
            detail = recorder.get(argument)
            if detail is None:
                yield event.plain_result(t("trace.not_found", id=argument))
                return
            yield event.plain_result(self._format_trace_detail(detail))
        except Exception as e:
            logger.error(f"读取召回追踪失败: {e}", exc_info=True)
            yield event.plain_result(t("trace.error", error=e))

    @staticmethod
    def _format_trace_time(timestamp: Any) -> str:
        """把 unix 时间戳格式化成命令展示用的本地时间。"""
        try:
            return datetime.fromtimestamp(float(timestamp)).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "-"

    def _format_trace_list(self, recorder, count: int) -> str:
        """格式化最近召回追踪列表。"""
        rows = recorder.recent(count)
        if not rows:
            return t("trace.empty")
        lines = [t("trace.header", count=len(rows))]
        for index, row in enumerate(rows, 1):
            routes = row.get("routes") or {}
            route_text = ",".join(
                f"{name}:{int(info.get('candidates') or 0)}"
                for name, info in routes.items()
            )
            lines.append(
                t(
                    "trace.item",
                    index=index,
                    id=row.get("id"),
                    time=self._format_trace_time(row.get("ts")),
                    session=row.get("session_id") or "-",
                    query=row.get("query_hash") or "-",
                    cache=t("trace.cache_hit")
                    if row.get("cache_hit")
                    else t("trace.cache_miss"),
                    routes=route_text or "-",
                    candidates=int(row.get("candidates") or 0),
                    injected=int(row.get("injected") or 0),
                    chars=int(row.get("injected_chars") or 0),
                    duration=row.get("duration_ms") or 0,
                )
            )
        return "\n".join(lines)

    def _format_trace_detail(self, detail: dict[str, Any]) -> str:
        """格式化单条召回追踪详情：路由、过滤原因、候选与注入。"""
        lines = [
            t(
                "trace.detail_header",
                id=detail.get("id"),
                time=self._format_trace_time(detail.get("ts")),
                session=detail.get("session_id") or "-",
                query=detail.get("query_hash") or "-",
                cache=t("trace.cache_hit")
                if detail.get("cache_hit")
                else t("trace.cache_miss"),
                duration=detail.get("duration_ms") or 0,
                candidates=len(detail.get("candidates") or []),
            )
        ]
        for name, route in (detail.get("routes") or {}).items():
            error = route.get("error")
            lines.append(
                t(
                    "trace.detail_route",
                    name=name,
                    candidates=int(route.get("candidates") or 0),
                    ok=bool(route.get("ok")),
                    ms=route.get("ms") or 0,
                    error=f" error={error}" if error else "",
                )
            )
        filters = detail.get("filters") or {}
        rejected = filters.get("rejected") or {}
        rejected_text = ",".join(
            f"{reason}:{int(value)}" for reason, value in rejected.items()
        )
        lines.append(
            t(
                "trace.detail_filters",
                min_similarity=filters.get("min_similarity", 0.0),
                min_importance=filters.get("min_importance", 0.0),
                rejected=rejected_text or "-",
            )
        )
        for candidate in (detail.get("candidates") or [])[:5]:
            lines.append(
                t(
                    "trace.detail_candidate",
                    doc_id=candidate.get("doc_id"),
                    score=candidate.get("final_score"),
                    bm25=candidate.get("bm25"),
                    vector=candidate.get("vector"),
                )
            )
        injected = detail.get("injected") or []
        lines.append(
            t(
                "trace.detail_injected",
                count=len(injected),
                chars=int(detail.get("injected_chars") or 0),
            )
        )
        notes = detail.get("notes") or []
        if notes:
            lines.append(t("trace.detail_notes", notes="; ".join(notes)))
        if detail.get("error"):
            lines.append(t("trace.error", error=detail.get("error")))
        return "\n".join(lines)

    async def handle_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem search 命令"""
        # 输入验证
        if not query or not query.strip():
            yield event.plain_result(t("search.query_empty"))
            return

        # 限制k的范围为1-100
        k = max(1, min(k, 100))

        # 与召回、反思、Agent 工具共用同一套白名单/人格/作用域解析，
        # 避免 global/user 作用域下命令搜不到数据。
        access = await resolve_event_memory_access(
            self.config_manager, self.context, event
        )
        if not access.allowed:
            yield event.plain_result(t("search.access_denied"))
            return

        try:
            with trace_request_context(
                session_id=event.unified_msg_origin,
                scope=access.scope,
                persona_id=access.persona_id,
            ):
                results = await self.memory_engine.search_memories(
                    query=query.strip(),
                    k=k,
                    session_id=access.scope,
                    persona_id=access.persona_id,
                )

            if not results:
                yield event.plain_result(t("search.no_results", query=query))
                return

            message = t("search.header", count=len(results))
            for i, result in enumerate(results, 1):
                score = result.final_score
                content = (
                    result.content[:100] + "..."
                    if len(result.content) > 100
                    else result.content
                )
                raw_breakdown = getattr(result, "score_breakdown", {})
                breakdown = raw_breakdown if isinstance(raw_breakdown, dict) else {}
                message += t(
                    "search.item.score",
                    index=i,
                    score=score,
                    content=content,
                )
                message += t("search.item.id", id=result.doc_id)
                message += t(
                    "search.item.breakdown",
                    doc_kw=breakdown.get("document_keyword_score", 0.0),
                    doc_vec=breakdown.get("document_vector_score", 0.0),
                    graph_kw=breakdown.get("graph_keyword_score", 0.0),
                    graph_vec=breakdown.get("graph_vector_score", 0.0),
                )

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("search.action_name"),
                    e,
                    t_list("error.suggestions.search"),
                )
            )

    async def handle_forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem forget 命令"""
        # 输入验证
        if doc_id < 0:
            yield event.plain_result(t("forget.id_invalid"))
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                yield event.plain_result(t("forget.success", id=doc_id))
            else:
                yield event.plain_result(t("forget.not_found", id=doc_id))
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("forget.action_name"),
                    e,
                    t_list("error.suggestions.forget"),
                )
            )

    async def handle_rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem rebuild-index 命令"""
        try:
            yield event.plain_result(t("rebuild_index.checking"))

            # 索引维护单入口：直接重建/补写，不再预先做全表一致性比对
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                partial_notice = ""
                if result.get("partial"):
                    partial_notice = t(
                        "rebuild_index.partial_notice",
                        ratio=result.get("failure_ratio", 0),
                    )
                switched_str = (
                    t("common.yes") if result.get("switched") else t("common.no")
                )
                result_msg = t(
                    "rebuild_index.result_template",
                    success=result["processed"],
                    failed=result["errors"],
                    total=result["total"],
                    vector_mode=result.get("vector_mode", "unknown"),
                    switched=switched_str,
                    partial_notice=partial_notice,
                )
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result(
                    t(
                        "rebuild_index.failed",
                        message=result.get("message", t("common.unknown_error")),
                    )
                )

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("rebuild_index.action_name"),
                    e,
                    t_list("error.suggestions.rebuild_index"),
                )
            )

    def _resolve_target_scope(self, event: AstrMessageEvent, raw: str) -> str:
        """把用户输入解析为目标/来源作用域。

        ``global`` / ``session`` / ``user`` 为关键字，分别解析为全局作用域、
        当前会话的 unified_msg_origin、以及基于当前事件身份构造的用户作用域；
        其余输入按字面作用域原样使用（便于迁移到自定义作用域）。

        ``user`` 需要传入 ``config_manager`` 才能应用 ``access_control.identity_aliases``，
        否则算出的作用域与运行时实际读写的作用域不一致，迁移后的数据仍然失联。
        """
        value = (raw or "").strip()
        if not value:
            return ""

        key = value.casefold()
        if key == "global":
            return GLOBAL_MEMORY_SCOPE
        if key == "session":
            return str(getattr(event, "unified_msg_origin", "") or "")
        if key == "user":
            return resolve_user_memory_scope(self.config_manager, event)
        return value

    def _scope_migration_backup_path(self) -> Path:
        """迁移备份路径：数据库同级 backups 目录，带微秒时间戳避免同秒冲突。"""
        db_path = Path(getattr(self.memory_engine, "db_path", "livingmemory.db"))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return db_path.parent / "backups" / f"scope_migration_{stamp}.db"

    async def handle_migrate_scope(
        self,
        event: AstrMessageEvent,
        target: str = "",
        action: str = "",
        source: str = "",
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem migrate-scope 命令。

        用法::

            /lmem migrate-scope <target> [apply] [source]

        AstrBot 的命令过滤器按位置解析参数并丢弃空 token
        （``astrbot/core/star/filter/command.py``），因此 ``apply`` 必须排在可选
        ``source`` 之前：``/lmem migrate-scope global apply`` 才是批量执行，
        ``/lmem migrate-scope global apply <source>`` 仅迁移指定来源。

        默认仅预览（dry-run），传 ``apply`` 才真正写库（写库前自动备份）；第二步
        也接受 ``dry-run`` 以显式预览。``source`` 传 ``all`` / ``*`` / ``-`` 表示
        全部来源，其余按 ``global`` / ``session`` / ``user`` 关键字或字面作用域解析。
        """
        try:
            resolved = self._resolve_target_scope(event, target)
            if not resolved:
                yield event.plain_result(t("migrate_scope.usage"))
                return

            mode = (action or "").strip().casefold()
            if mode in {"apply", "yes", "confirm"}:
                apply_changes = True
            elif mode in {"", "dry-run", "dry_run", "preview"}:
                apply_changes = False
            else:
                # 旧参数顺序或拼写错误：给出正确用法，避免被误当成 source 过滤
                yield event.plain_result(t("migrate_scope.usage"))
                return

            raw_source = (source or "").strip()
            if raw_source.casefold() in {"", "all", "*", "-"}:
                source_scope = None
            else:
                source_scope = self._resolve_target_scope(event, raw_source)

            db_connection = getattr(self.memory_engine, "db_connection", None)
            migrator = ScopeMigrator(db_connection)
            breakdown = await migrator.survey()
            plan = await migrator.plan(resolved, source=source_scope)

            scopes_detail = "\n".join(
                t(
                    "migrate_scope.scope_line",
                    scope=scope or t("migrate_scope.empty_scope"),
                    documents=breakdown.documents.get(scope, 0),
                    atoms=breakdown.atoms.get(scope, 0),
                    graph_entries=breakdown.graph_entries.get(scope, 0),
                )
                for scope in breakdown.scopes
            )
            yield event.plain_result(
                t(
                    "migrate_scope.survey",
                    scopes=scopes_detail,
                    total=breakdown.total,
                )
            )
            if breakdown.missing_tables:
                yield event.plain_result(
                    t(
                        "migrate_scope.missing_tables",
                        tables=", ".join(breakdown.missing_tables),
                    )
                )

            if plan.is_noop:
                if source_scope is not None:
                    # 来源过滤没命中，与"全部记录已在目标作用域"是两回事，不能混报
                    yield event.plain_result(
                        t(
                            "migrate_scope.source_matched_nothing",
                            target=resolved,
                            source=source_scope,
                        )
                    )
                else:
                    yield event.plain_result(
                        t("migrate_scope.nothing_to_do", target=resolved)
                    )
                return

            impact_detail = "\n".join(
                t(
                    "migrate_scope.impact_line",
                    scope=scope or t("migrate_scope.empty_scope"),
                    count=count,
                )
                for scope, count in sorted(plan.by_source.items())
            )
            yield event.plain_result(
                t(
                    "migrate_scope.plan",
                    target=resolved,
                    documents=plan.documents,
                    atoms=plan.atoms,
                    graph_entries=plan.graph_entries,
                    total=plan.total,
                    source=source_scope or t("migrate_scope.any_source"),
                    impact=impact_detail,
                )
            )

            if not apply_changes:
                yield event.plain_result(t("migrate_scope.dry_run_hint"))
                return

            result = await migrator.execute(
                plan, backup_path=self._scope_migration_backup_path()
            )
            yield event.plain_result(
                t(
                    "migrate_scope.done",
                    target=result.target,
                    documents=result.documents,
                    atoms=result.atoms,
                    graph_entries=result.graph_entries,
                    total=result.total,
                    backup=result.backup_path or t("common.never"),
                )
            )
            if result.remaining:
                yield event.plain_result(
                    t(
                        "migrate_scope.remaining_notice",
                        target=result.target,
                        remaining=result.remaining,
                    )
                )
            yield event.plain_result(t("migrate_scope.rebuild_hint"))

        except Exception as e:
            logger.error(f"作用域迁移失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("migrate_scope.action_name"),
                    e,
                    t_list("error.suggestions.migrate_scope"),
                )
            )

    async def handle_rebuild_graph(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem rebuild-graph 命令"""
        try:
            yield event.plain_result(t("rebuild_graph.starting"))
            result = await self.memory_engine.rebuild_graph_index()
            yield event.plain_result(
                t(
                    "rebuild_graph.success",
                    rebuilt=result.get("rebuilt", 0),
                    skipped=result.get("skipped", 0),
                )
            )
        except Exception as e:
            logger.error(f"重建图记忆失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("rebuild_graph.action_name"),
                    e,
                    t_list("error.suggestions.rebuild_graph"),
                )
            )

    async def handle_webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem webui 命令"""
        yield event.plain_result(t("webui.guide"))

    async def handle_summarize(
        self, event: AstrMessageEvent, message_count: int | None = None
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem summarize 命令 - 立即触发记忆总结"""
        session_id = event.unified_msg_origin
        try:
            if not is_event_memory_allowed(self.config_manager, event):
                logger.debug("当前事件不在记忆白名单中，跳过手动总结")
                yield event.plain_result(t("summarize.access_denied"))
                return

            # 获取当前消息数和总结进度
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )
            try:
                last_summarized_index = int(last_summarized_index)
            except (TypeError, ValueError):
                last_summarized_index = 0

            if message_count is not None:
                try:
                    requested_count = int(message_count)
                except (TypeError, ValueError):
                    requested_count = 0
                if requested_count < 2:
                    yield event.plain_result(t("summarize.invalid_count"))
                    return
                last_summarized_index = max(0, actual_count - requested_count)

            unsummarized = actual_count - last_summarized_index

            if unsummarized < 2:
                yield event.plain_result(
                    t(
                        "summarize.no_new",
                        total=actual_count,
                        index=last_summarized_index,
                    )
                )
                return

            yield event.plain_result(
                t(
                    "summarize.starting",
                    start=last_summarized_index,
                    end=actual_count,
                    count=unsummarized,
                )
            )

            history_messages = await self.conversation_manager.get_messages_range(
                session_id=session_id,
                start_index=last_summarized_index,
                end_index=actual_count,
            )

            if not history_messages:
                yield event.plain_result(t("summarize.fetch_failed"))
                return

            # 获取 persona_id
            from .utils import get_persona_id

            persona_id = await get_persona_id(self.context, event)
            memory_scope = (
                resolve_memory_scope(self.config_manager, event) or session_id
            )

            # 判断是否群聊
            is_group_chat = bool(
                history_messages[0].group_id if history_messages else False
            )
            if not is_group_chat and "GroupMessage" in session_id:
                is_group_chat = True

            (
                content,
                metadata,
                importance,
            ) = await self._memory_processor.process_conversation(
                messages=history_messages,
                is_group_chat=is_group_chat,
                persona_id=persona_id,
            )

            atoms = self._memory_processor.classify_atoms_from_metadata(
                metadata=metadata,
                parent_importance=importance,
                session_id=memory_scope,
                persona_id=persona_id,
            )

            metadata["source_window"] = {
                "session_id": session_id,
                "start_index": last_summarized_index,
                "end_index": actual_count,
                "message_count": actual_count - last_summarized_index,
                "triggered_by": "manual",
            }
            metadata["source_session_id"] = session_id
            profile_updates = metadata.pop("profile_updates", []) or []

            memory_id = await self.memory_engine.add_memory(
                content=content,
                session_id=memory_scope,
                persona_id=persona_id,
                importance=importance,
                metadata=metadata,
                atoms=atoms,
                source_messages=(
                    serialize_source_messages(history_messages)
                    if importance
                    >= float(
                        self.config_manager.get(
                            "reflection_engine.source_retention_importance_threshold",
                            0.8,
                        )
                    )
                    else None
                ),
            )

            if profile_updates and memory_id is not None:
                await self._apply_manual_profile_updates(
                    memory_scope,
                    persona_id,
                    profile_updates,
                    memory_id,
                )

            await self.conversation_manager.update_session_metadata(
                session_id, "last_summarized_index", actual_count
            )
            await self.conversation_manager.update_session_metadata(
                session_id, "pending_summary", None
            )

            topics = ", ".join(metadata.get("topics", [])) or t("common.none")
            yield event.plain_result(
                t(
                    "summarize.success",
                    importance=importance,
                    topics=topics,
                    count=actual_count,
                )
            )

        except Exception as e:
            logger.error(f"手动触发记忆总结失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("summarize.action_name"),
                    e,
                    t_list("error.suggestions.summarize"),
                )
            )

    async def _apply_manual_profile_updates(
        self,
        memory_scope: str,
        persona_id,
        updates: list,
        memory_id: int,
    ) -> None:
        """Apply profile updates produced by a manual summary.

        Args:
            memory_scope: Resolved memory scope.
            persona_id: Persona id of the summary.
            updates: Normalised profile updates from the summary metadata.
            memory_id: Memory written by this manual summary (evidence id).
        """
        manager = getattr(self.memory_engine, "profile_manager", None)
        if manager is None:
            return
        try:
            result = await manager.apply_updates(
                memory_scope,
                persona_id,
                updates,
                evidence_memory_ids=[int(memory_id)],
                source="manual_summary",
            )
        except Exception:
            logger.warning(
                "手动总结的用户档案更新失败（不影响记忆入库）", exc_info=True
            )
            return
        logger.info(
            "手动总结的用户档案更新: applied=%s, pending=%s, version=%s",
            result.get("applied", 0),
            result.get("pending", 0),
            result.get("version"),
        )

    async def handle_reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem reset 命令"""
        session_id = event.unified_msg_origin
        try:
            await self.conversation_manager.clear_session(session_id)
            message = t("reset.success")
            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("reset.action_name"),
                    e,
                    t_list("error.suggestions.reset"),
                )
            )

    async def handle_cleanup(
        self, event: AstrMessageEvent, dry_run: bool = False
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem cleanup 命令 - 清理 AstrBot 历史消息中的记忆注入片段"""
        session_id = event.unified_msg_origin
        try:
            mode_text = t("cleanup.mode_preview") if dry_run else t("cleanup.mode_exec")
            yield event.plain_result(t("cleanup.starting", mode_text=mode_text))

            # 检查 context 是否可用
            if not self.context:
                yield event.plain_result(t("cleanup.context_unavailable"))
                return

            # 获取当前对话 ID
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                session_id
            )
            if not cid:
                yield event.plain_result(t("cleanup.no_history"))
                return

            # 获取对话历史
            conversation = await self.context.conversation_manager.get_conversation(
                session_id, cid
            )
            if not conversation or not conversation.history:
                yield event.plain_result(t("cleanup.empty_history"))
                return

            # 清理历史消息中的记忆注入片段
            import json
            import re

            from .base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

            # 解析 history（字符串格式）
            try:
                history = json.loads(conversation.history)
            except json.JSONDecodeError:
                yield event.plain_result(t("cleanup.parse_failed"))
                return

            # 统计信息
            stats = {
                "scanned": len(history),
                "matched": 0,
                "cleaned": 0,
                "deleted": 0,
            }

            # 编译清理正则
            pattern = re.compile(
                re.escape(MEMORY_INJECTION_HEADER)
                + r".*?"
                + re.escape(MEMORY_INJECTION_FOOTER),
                flags=re.DOTALL,
            )

            # 清理历史消息
            cleaned_history = []
            for msg in history:
                content = msg.get("content", "")
                if not isinstance(content, str):
                    cleaned_history.append(msg)
                    continue

                # 检查是否包含注入标记
                if (
                    MEMORY_INJECTION_HEADER in content
                    and MEMORY_INJECTION_FOOTER in content
                ):
                    stats["matched"] += 1

                    # 清理内容
                    cleaned_content = pattern.sub("", content)
                    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

                    # 如果清理后为空，跳过该消息
                    if not cleaned_content:
                        stats["deleted"] += 1
                        logger.debug(
                            f"[cleanup] 删除纯记忆注入消息: role={msg.get('role')}"
                        )
                        continue

                    # 如果清理后仍有内容，保留清理后的消息
                    if cleaned_content != content:
                        msg_copy = msg.copy()
                        msg_copy["content"] = cleaned_content
                        cleaned_history.append(msg_copy)
                        stats["cleaned"] += 1
                        logger.debug(
                            f"[cleanup] 清理消息内部记忆片段: "
                            f"原长度={len(content)}, 新长度={len(cleaned_content)}"
                        )
                        continue

                cleaned_history.append(msg)

            # 如果不是预演模式，更新数据库
            if not dry_run and (stats["cleaned"] > 0 or stats["deleted"] > 0):
                await self.context.conversation_manager.update_conversation(
                    unified_msg_origin=session_id,
                    conversation_id=cid,
                    history=cleaned_history,
                )
                logger.info(
                    f"[{session_id}] cleanup 已更新 AstrBot 对话历史: "
                    f"清理={stats['cleaned']}, 删除={stats['deleted']}"
                )

            # 格式化结果
            notice = (
                t("cleanup.notice_preview") if dry_run else t("cleanup.notice_exec")
            )
            message = t(
                "cleanup.result_template",
                mode_text=mode_text,
                scanned=stats["scanned"],
                matched=stats["matched"],
                cleaned=stats["cleaned"],
                deleted=stats["deleted"],
                notice=notice,
            )

            yield event.plain_result(message)

        except Exception as e:
            logger.error(f"清理历史消息失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("cleanup.action_name"),
                    e,
                    t_list("error.suggestions.cleanup"),
                )
            )

    async def handle_help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem help 命令"""
        message = t("help.text")
        yield event.plain_result(message)
