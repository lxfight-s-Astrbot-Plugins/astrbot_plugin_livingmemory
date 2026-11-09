"""召回追踪（recall trace）与记忆血缘（lineage）只读查询处理器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

from ..memory_scope import adapter_sql
from ..utils.json_utils import safe_json_dict

if TYPE_CHECKING:
    from .utils import PageApiUtils

_MAX_TRACE_LIMIT = 50
_MAX_LINEAGE_SUPERSEDES = 20
_MAX_SUPERSEDE_CHAIN = 5
_LINEAGE_GRAPH_LIMITS = {
    "limit_entries": 36,
    "limit_nodes": 48,
    "limit_edges": 72,
}


class TraceHandler:
    """召回追踪与记忆血缘处理器。"""

    def __init__(self, utils: PageApiUtils):
        """初始化处理器。

        Args:
            utils: Page API 工具实例。
        """
        self.utils = utils

    @staticmethod
    def _recorder(memory_engine) -> Any:
        """返回引擎上的追踪记录器（可能不存在）。"""
        return getattr(memory_engine, "trace_recorder", None)

    def _disabled_payload(self) -> dict[str, Any]:
        return self.utils.ok({"enabled": False, "traces": [], "trace": None})

    async def list_traces(self, memory_engine) -> dict[str, Any]:
        """列出最近的召回追踪摘要。

        Query:
            limit: 返回条数（默认 20，最大 50）。
            session_id: 可选会话过滤。

        Returns:
            最近追踪摘要列表与基础统计。
        """
        recorder = self._recorder(memory_engine)
        if recorder is None or not recorder.enabled:
            return self._disabled_payload()

        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            return self.utils.error("limit 必须是整数")
        limit = max(1, min(limit, _MAX_TRACE_LIMIT))
        session_id = self.utils.optional_text(request.args.get("session_id"))
        try:
            traces = recorder.recent(limit, session_id=session_id)
            stats = recorder.stats()
        except Exception as exc:
            logger.error(f"[PageAPI] 读取召回追踪失败: {exc}", exc_info=True)
            return self.utils.error("读取召回追踪失败")
        return self.utils.ok(
            {
                "enabled": True,
                "traces": traces,
                "count": len(traces),
                "max_records": int(stats.get("max_records") or 0),
                "total_records": int(stats.get("records") or 0),
                "persist_enabled": bool(stats.get("persist_enabled")),
            }
        )

    async def get_trace(self, memory_engine, trace_id: str) -> dict[str, Any]:
        """返回单条追踪详情。

        Args:
            memory_engine: 记忆引擎。
            trace_id: 追踪 id（路径参数）。

        Returns:
            追踪详情；不存在时返回错误。
        """
        recorder = self._recorder(memory_engine)
        if recorder is None or not recorder.enabled:
            return self._disabled_payload()
        normalized = str(trace_id or "").strip()
        if not normalized:
            return self.utils.error("trace_id 不能为空")
        try:
            trace = recorder.get(normalized)
        except Exception as exc:
            logger.error(f"[PageAPI] 读取召回追踪详情失败: {exc}", exc_info=True)
            return self.utils.error("读取召回追踪详情失败")
        if trace is None:
            return self.utils.error("追踪记录不存在")
        return self.utils.ok({"enabled": True, "trace": trace})

    async def get_trace_stats(self, memory_engine) -> dict[str, Any]:
        """返回召回追踪的聚合统计（路由/过滤原因/缓存命中/落盘）。"""
        recorder = self._recorder(memory_engine)
        if recorder is None or not recorder.enabled:
            return self.utils.ok({"enabled": False})
        try:
            return self.utils.ok(recorder.stats())
        except Exception as exc:
            logger.error(f"[PageAPI] 读取召回追踪统计失败: {exc}", exc_info=True)
            return self.utils.error("读取召回追踪统计失败")

    async def get_lineage(self, memory_engine, memory_id: str) -> dict[str, Any]:
        """返回一条记忆的血缘：metadata、来源消息、取代链、图关联与命中追踪。

        Args:
            memory_engine: 记忆引擎。
            memory_id: 记忆 id（路径参数）。

        Returns:
            只读血缘结构；记忆不存在时返回错误。
        """
        try:
            target = int(str(memory_id or "").strip())
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")

        memory = await memory_engine.get_memory_record(target)
        if not memory:
            return self.utils.error("记忆不存在")
        metadata = safe_json_dict(memory.get("metadata"))

        sources: list[dict[str, Any]] = []
        try:
            sources = await memory_engine.get_memory_source(target) or []
        except Exception as exc:
            logger.warning(f"[PageAPI] 读取记忆来源失败 (memory_id={target}): {exc}")

        chain = await self._supersede_chain(memory_engine, target, metadata)
        supersedes = await self._supersedes_of(memory_engine, target)
        graph = await self._lineage_graph(memory_engine, target)
        traces = self._citing_traces(memory_engine, target)

        return self.utils.ok(
            {
                "memory_id": target,
                "memory": {
                    "id": memory.get("id"),
                    "doc_id": memory.get("doc_id"),
                    "text": memory.get("text"),
                    "created_at": memory.get("created_at"),
                    "updated_at": memory.get("updated_at"),
                    "session_id": metadata.get("session_id"),
                    "persona_id": metadata.get("persona_id"),
                    "adapter_id": metadata.get("adapter_id"),
                    "status": metadata.get("status") or "active",
                    "importance": metadata.get("importance"),
                    "create_time": metadata.get("create_time"),
                    "metadata": metadata,
                },
                "sources": {
                    "count": len(sources),
                    "messages": sources,
                    "source_window": metadata.get("source_window"),
                    "source_windows": metadata.get("source_windows") or [],
                    "has_source": bool(metadata.get("has_source")),
                    "source_message_count": metadata.get("source_message_count"),
                },
                "supersedes": {
                    "chain": chain,
                    "superseded_by": metadata.get("superseded_by"),
                    "superseded_at": metadata.get("superseded_at"),
                    "of": supersedes,
                },
                "links": {
                    "previous_id": metadata.get("previous_id"),
                    "parent_memory_id": metadata.get("parent_memory_id"),
                    "related_ids": metadata.get("related_ids") or [],
                    "memory_import_key": metadata.get("memory_import_key"),
                },
                "graph": graph,
                "traces": traces,
            }
        )

    async def _supersede_chain(
        self, memory_engine, target: int, metadata: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """沿 ``superseded_by`` 向后追溯被取代链（最多 5 级）。"""
        chain: list[dict[str, Any]] = []
        seen = {int(target)}
        current_meta = metadata
        current_id: int | None = target
        for _ in range(_MAX_SUPERSEDE_CHAIN):
            if current_id is None:
                break
            next_id = current_meta.get("superseded_by")
            try:
                next_id_int = int(next_id) if next_id is not None else None
            except (TypeError, ValueError):
                next_id_int = None
            chain.append(
                {
                    "id": current_id,
                    "superseded_by": next_id_int,
                    "superseded_at": current_meta.get("superseded_at"),
                }
            )
            if next_id_int is None or next_id_int in seen:
                break
            seen.add(next_id_int)
            record = await memory_engine.get_memory_record(next_id_int)
            if not record:
                break
            current_id = next_id_int
            current_meta = safe_json_dict(record.get("metadata"))
        return chain

    async def _supersedes_of(
        self, memory_engine, memory_id: int
    ) -> list[dict[str, Any]]:
        """查询哪些较新记忆把当前记忆标记为已取代。"""
        db = getattr(memory_engine, "db_connection", None)
        if db is None:
            return []
        conditions = ["json_extract(metadata, '$.superseded_by') = ?"]
        params: list[Any] = [int(memory_id)]
        ownership, ownership_params = adapter_sql("metadata")
        conditions.append(ownership)
        params.extend(ownership_params)
        try:
            cursor = await db.execute(
                "SELECT id, text FROM documents WHERE "
                + " AND ".join(conditions)
                + " LIMIT ?",
                (*params, _MAX_LINEAGE_SUPERSEDES),
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.warning(f"[PageAPI] 查询取代关系失败 (memory_id={memory_id}): {exc}")
            return []
        return [{"id": int(row[0]), "text": str(row[1] or "")[:200]} for row in rows]

    async def _lineage_graph(
        self, memory_engine, memory_id: int
    ) -> dict[str, Any] | None:
        """返回该记忆周围的受限图快照（不可用时为 None）。"""
        graph_store = self.utils.get_graph_store(memory_engine)
        if graph_store is None:
            return None
        try:
            return await graph_store.get_subgraph_for_memories(
                [int(memory_id)], **_LINEAGE_GRAPH_LIMITS
            )
        except Exception as exc:
            logger.warning(
                f"[PageAPI] 读取记忆图谱血缘失败 (memory_id={memory_id}): {exc}"
            )
            return None

    def _citing_traces(self, memory_engine, memory_id: int) -> list[dict[str, Any]]:
        """返回环形缓冲内命中该记忆的最近追踪摘要。"""
        recorder = self._recorder(memory_engine)
        if recorder is None or not recorder.enabled:
            return []
        try:
            return recorder.find_citing(int(memory_id), limit=10)
        except Exception as exc:
            logger.warning(
                f"[PageAPI] 查询记忆追踪命中失败 (memory_id={memory_id}): {exc}"
            )
            return []


__all__ = ["TraceHandler"]
