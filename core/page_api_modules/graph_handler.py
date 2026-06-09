"""
图谱查询处理模块
"""

from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

if TYPE_CHECKING:
    from .utils import PageApiUtils


class GraphHandler:
    """图谱查询处理器"""

    def __init__(self, utils: "PageApiUtils"):
        """
        初始化图谱查询处理器

        Args:
            utils: PageApiUtils 工具实例
        """
        self.utils = utils

    @staticmethod
    def _add_memory_id(
        memory_ids: list[int],
        seen_memory_ids: set[int],
        value: Any,
    ) -> int | None:
        try:
            memory_id = int(value)
        except (TypeError, ValueError):
            return None
        if memory_id not in seen_memory_ids:
            seen_memory_ids.add(memory_id)
            memory_ids.append(memory_id)
        return memory_id

    async def get_graph_overview(self, memory_engine) -> dict[str, Any]:
        """
        获取图谱概览

        查询参数:
            - session_id: 会话ID过滤（可选）
            - persona_id: 人格ID过滤（可选）
            - limit_memories: 记忆数量限制（默认12，最大24）
            - limit_entries: 入口数量限制（默认36，最大80）
            - limit_nodes: 节点数量限制（默认48，最大80）
            - limit_edges: 边数量限制（默认72，最大120）

        Returns:
            包含图谱快照和统计的字典
        """
        args = request.args
        session_id = self.utils.optional_text(args.get("session_id"))
        persona_id = self.utils.optional_text(args.get("persona_id"))

        try:
            limit_memories = max(1, min(int(args.get("limit_memories", 12)), 24))
            limit_entries = max(12, min(int(args.get("limit_entries", 36)), 80))
            limit_nodes = max(12, min(int(args.get("limit_nodes", 48)), 80))
            limit_edges = max(12, min(int(args.get("limit_edges", 72)), 120))
        except (TypeError, ValueError):
            return self.utils.error("图谱分页参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self.utils.get_graph_store(memory_engine)
            empty_snapshot = {
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
            if graph_store is None:
                return self.utils.ok(
                    self.utils.build_graph_view_payload(
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
            return self.utils.ok(
                self.utils.build_graph_view_payload(
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
            return self.utils.error(str(exc))

    async def query_graph(self, memory_engine) -> dict[str, Any]:
        """
        查询图谱

        支持三种模式:
        1. memory_focus: 基于特定记忆ID的子图
        2. query: 基于查询文本的语义检索
        3. overview: 无查询时的概览

        Payload:
            - query: 查询文本（可选）
            - memory_id: 记忆ID（可选）
            - session_id: 会话ID过滤（可选）
            - persona_id: 人格ID过滤（可选）
            - limit_memories: 记忆数量限制（默认10，最大24）
            - limit_entries: 入口数量限制（默认40，最大80）
            - limit_nodes: 节点数量限制（默认56，最大80）
            - limit_edges: 边数量限制（默认96，最大120）

        Returns:
            包含图谱快照、检索结果和高亮信息的字典
        """
        payload = await request.get_json(silent=True) or {}
        query_text = str(payload.get("query", "")).strip()
        session_id = self.utils.optional_text(payload.get("session_id"))
        persona_id = self.utils.optional_text(payload.get("persona_id"))
        memory_id_raw = payload.get("memory_id")

        try:
            limit_memories = max(1, min(int(payload.get("limit_memories", 10)), 24))
            limit_entries = max(12, min(int(payload.get("limit_entries", 40)), 80))
            limit_nodes = max(12, min(int(payload.get("limit_nodes", 56)), 80))
            limit_edges = max(12, min(int(payload.get("limit_edges", 96)), 120))
        except (TypeError, ValueError):
            return self.utils.error("图谱检索参数无效")

        try:
            stats = await memory_engine.get_statistics()
            graph_store = self.utils.get_graph_store(memory_engine)
            empty_snapshot = {
                "nodes": [],
                "edges": [],
                "entries": [],
                "memories": [],
            }
            if graph_store is None:
                return self.utils.ok(
                    self.utils.build_graph_view_payload(
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

            # 模式 1: 基于特定记忆ID
            if memory_id_raw not in (None, ""):
                try:
                    memory_id = int(memory_id_raw)
                except (TypeError, ValueError):
                    return self.utils.error("memory_id 必须是整数")

                snapshot = await graph_store.get_subgraph_for_memories(
                    [memory_id],
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self.utils.ok(
                    self.utils.build_graph_view_payload(
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

            # 模式 2: 无查询，返回概览
            if not query_text:
                snapshot = await graph_store.get_graph_snapshot(
                    session_id=session_id,
                    persona_id=persona_id,
                    limit_memories=limit_memories,
                    limit_entries=limit_entries,
                    limit_nodes=limit_nodes,
                    limit_edges=limit_edges,
                )
                return self.utils.ok(
                    self.utils.build_graph_view_payload(
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

            # 模式 3: 基于查询文本的语义检索
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
                memory_id = self._add_memory_id(
                    matched_memory_ids,
                    seen_memory_ids,
                    result.doc_id,
                )
                if memory_id is None:
                    continue
                retrieval_items.append(
                    {
                        "memory_id": memory_id,
                        "content": result.content,
                        "metadata": result.metadata,
                        "final_score": round(float(result.final_score), 6),
                        "rrf_score": (
                            round(float(result.rrf_score), 6)
                            if getattr(result, "rrf_score", None) is not None
                            else None
                        ),
                        "bm25_score": (
                            round(float(result.bm25_score), 6)
                            if getattr(result, "bm25_score", None) is not None
                            else None
                        ),
                        "vector_score": (
                            round(float(result.vector_score), 6)
                            if getattr(result, "vector_score", None) is not None
                            else None
                        ),
                        "score_breakdown": {
                            key: round(float(value), 6)
                            for key, value in (
                                getattr(result, "score_breakdown", None) or {}
                            ).items()
                            if isinstance(value, (int, float))
                        },
                    }
                )

            # 基于查询分词查找相关节点
            tokens = self.utils.tokenize_graph_query(query_text)
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
                    memory_id = self._add_memory_id(
                        matched_memory_ids,
                        seen_memory_ids,
                        hit.get("source_memory_id"),
                    )
                    if memory_id is None:
                        continue
                    if not any(
                        item.get("memory_id") == memory_id for item in retrieval_items
                    ):
                        retrieval_items.append(
                            {
                                "memory_id": memory_id,
                                "content": hit.get("content", ""),
                                "metadata": hit.get("metadata") or {},
                                "final_score": round(float(hit.get("score", 0.0)), 6),
                                "rrf_score": None,
                                "bm25_score": None,
                                "vector_score": None,
                                "score_breakdown": {
                                    "graph_node": round(
                                        float(hit.get("score", 0.0)),
                                        6,
                                    ),
                                },
                                "source": "graph_node",
                                "entry_id": hit.get("entry_id"),
                                "matched_node_ids": matched_node_ids,
                            }
                        )

            snapshot = await graph_store.get_subgraph_for_memories(
                matched_memory_ids[:limit_memories],
                limit_entries=limit_entries,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
            return self.utils.ok(
                self.utils.build_graph_view_payload(
                    snapshot,
                    stats,
                    enabled=True,
                    mode="query",
                    query=query_text,
                    retrieval_items=retrieval_items,
                    matched_node_ids=matched_node_ids,
                    matched_memory_ids=matched_memory_ids,
                    filters={
                        "session_id": session_id,
                        "persona_id": persona_id,
                    },
                )
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 图谱查询失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))
