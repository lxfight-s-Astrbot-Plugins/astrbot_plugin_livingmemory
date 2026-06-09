"""
Page API 工具函数模块
提供响应格式化、元数据处理等工具方法
"""

import json
from typing import Any

from ..utils.number_utils import safe_float


class PageApiUtils:
    """Page API 工具类"""

    @staticmethod
    def ok(data: Any = None) -> dict[str, Any]:
        """构造成功响应"""
        return {"status": "ok", "data": data}

    @staticmethod
    def error(message: str) -> dict[str, Any]:
        """构造错误响应"""
        return {"status": "error", "message": str(message)}

    @staticmethod
    def optional_text(value: Any) -> str | None:
        """Normalize optional request text without preserving empty sentinel values."""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "null", "undefined"}:
            return None
        return text

    @staticmethod
    def normalize_metadata(metadata: Any) -> dict[str, Any]:
        """规范化 metadata 为字典格式"""
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
    def importance_to_display(value: Any) -> float:
        """将重要性值转换为显示格式（0-10范围）"""
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = 0.5
        if parsed <= 1.0:
            parsed *= 10.0
        return round(max(0.0, min(10.0, parsed)), 2)

    @classmethod
    def append_update_history(
        cls,
        metadata: dict[str, Any],
        *,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        timestamp: float,
    ) -> list[dict[str, Any]]:
        """向 metadata 中添加更新历史记录"""
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
        """转换历史记录值为可序列化格式"""
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
        """生成历史记录的描述文本"""
        old_text = cls._short_history_text(old_value)
        new_text = cls._short_history_text(new_value)
        suffix = f" ({reason})" if reason else ""
        return f"{field}: {old_text} → {new_text}{suffix}"

    @staticmethod
    def _short_history_text(value: Any) -> str:
        """截断历史值为简短文本"""
        text = str(value if value is not None else "")
        text = " ".join(text.split())
        return text if len(text) <= 64 else f"{text[:61]}..."

    @staticmethod
    def get_graph_store(memory_engine):
        """从 memory_engine 获取 graph_store"""
        return getattr(memory_engine, "graph_store", None)

    @staticmethod
    def tokenize_graph_query(query: str) -> list[str]:
        """
        将图谱查询文本分词为搜索 token

        支持：
        - 英文单词分割
        - 中文整句和 n-gram 分割
        - 最多返回 12 个 token
        """
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
    def build_graph_view_payload(
        snapshot: dict[str, Any],
        stats: dict[str, Any],
        *,
        enabled: bool,
        mode: str,
        query: str | None = None,
        memory_id: int | None = None,
        retrieval_items: list[dict[str, Any]] | None = None,
        matched_node_ids: list[int] | None = None,
        matched_memory_ids: list[int] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        构建图谱视图的完整返回结构

        包含：
        - 节点、边、入口、记忆快照
        - 统计摘要和分类
        - 高亮和检索信息
        """
        nodes = [dict(item) for item in snapshot.get("nodes", [])]
        edges = [dict(item) for item in snapshot.get("edges", [])]
        entries = [dict(item) for item in snapshot.get("entries", [])]
        memories = [dict(item) for item in snapshot.get("memories", [])]
        retrieval_items = [dict(item) for item in (retrieval_items or [])]
        matched_node_ids = [int(item) for item in (matched_node_ids or [])]
        matched_memory_ids = [int(item) for item in (matched_memory_ids or [])]
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
            "matched_memory_ids": matched_memory_ids
            or [item["memory_id"] for item in retrieval_items],
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
