"""记忆库定期整合管理器。

把零散的低价值记忆聚合、整理、总结为更精炼的记忆，从源头控制记忆库规模。
聚合粒度（同会话 / 语义聚类）、旧记忆处理（归档 / 删除）、触发方式（每日 / 反思）
均由 memory_consolidation 配置控制。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger


class MemoryConsolidationManager:
    """按配置定期整合记忆库。"""

    def __init__(self, memory_engine, memory_processor, config_manager):
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.config_manager = config_manager
        # reflection 触发模式下的最小运行间隔（秒），避免每条消息都触发
        self._last_run_at = 0.0
        self._min_run_interval = 6 * 3600.0

    @property
    def config(self) -> dict[str, Any]:
        return self.config_manager.get_section("memory_consolidation")

    async def maybe_run(self, trigger: str) -> dict[str, Any]:
        """按触发方式执行整合（不匹配或未启用时跳过）。

        Args:
            trigger: "daily" 或 "reflection"。
        """
        cfg = self.config
        if not cfg.get("enabled", False) or cfg.get("trigger", "daily") != trigger:
            return {"skipped": True}
        return await self.run_consolidation(force=(trigger == "daily"))

    async def run_consolidation(self, force: bool = False) -> dict[str, Any]:
        """执行一轮记忆整合。返回统计信息。

        Args:
            force: 是否忽略最小运行间隔（每日定时触发时传入 True）。
        """
        cfg = self.config
        if not cfg.get("enabled", False):
            return {"skipped": True}
        if not self.memory_engine or not self.memory_processor:
            logger.warning("[记忆整合] 组件未就绪，跳过")
            return {"skipped": True, "reason": "components not ready"}

        now = time.time()
        if not force and now - self._last_run_at < self._min_run_interval:
            return {"skipped": True, "reason": "cooldown"}
        self._last_run_at = now

        try:
            candidates = await self._query_candidates(cfg)
            if not candidates:
                return {"candidates": 0, "groups": 0, "merged": 0}

            groups = await self._build_groups(candidates, cfg)
            if not groups:
                return {"candidates": len(candidates), "groups": 0, "merged": 0}

            max_groups = int(cfg.get("max_groups_per_run", 5))
            keep_original = cfg.get("keep_original", "archive")
            stats = {
                "candidates": len(candidates),
                "groups": 0,
                "merged": 0,
                "archived": 0,
                "deleted": 0,
                "failed": 0,
            }
            for group in groups[:max_groups]:
                try:
                    result = await self._consolidate_group(group, cfg)
                    stats["groups"] += 1
                    stats["merged"] += result["merged"]
                    if keep_original == "archive":
                        stats["archived"] += result["removed"]
                    else:
                        stats["deleted"] += result["removed"]
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"[记忆整合] 整合组失败: {e}", exc_info=True)

            logger.info(f"[记忆整合] 完成: {stats}")
            return stats
        except Exception as e:
            logger.error(f"[记忆整合] 运行失败: {e}", exc_info=True)
            return {"error": str(e)}

    async def _query_candidates(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """查询符合整合条件的候选记忆（低重要度 + 足够旧 + 活跃状态）。"""
        db = getattr(self.memory_engine, "db_connection", None)
        if db is None:
            return []

        max_importance = float(cfg.get("max_importance", 0.5))
        cutoff = time.time() - int(cfg.get("min_age_days", 7)) * 86400.0

        cursor = await db.execute(
            """
            SELECT id, text, metadata
            FROM documents
            WHERE COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'
              AND CAST(COALESCE(json_extract(metadata, '$.importance'), '0.5') AS REAL) < ?
              AND CAST(COALESCE(json_extract(metadata, '$.create_time'), '0') AS REAL) < ?
            ORDER BY id
            """,
            (max_importance, cutoff),
        )
        rows = await cursor.fetchall()

        safe_json_dict = self.memory_engine._safe_json_dict
        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidates.append(
                {
                    "id": int(row["id"]),
                    "content": row["text"],
                    "metadata": safe_json_dict(row["metadata"]),
                }
            )
        return candidates

    async def _build_groups(
        self, candidates: list[dict[str, Any]], cfg: dict[str, Any]
    ) -> list[list[dict[str, Any]]]:
        granularity = cfg.get("granularity", "session")
        if granularity == "semantic":
            groups = await self._group_semantic(candidates, cfg)
        else:
            groups = self._group_by_session(candidates)

        min_per = int(cfg.get("min_memories_per_group", 3))
        groups = [g for g in groups if len(g) >= min_per]
        groups.sort(key=len, reverse=True)
        return groups

    def _group_by_session(
        self, candidates: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for mem in candidates:
            session_id = mem["metadata"].get("session_id")
            if not session_id:
                continue
            grouped.setdefault(str(session_id), []).append(mem)
        return list(grouped.values())

    async def _group_semantic(
        self, candidates: list[dict[str, Any]], cfg: dict[str, Any]
    ) -> list[list[dict[str, Any]]]:
        """跨会话语义聚类：复用索引内向量批量查找相似对，再做连通分量合并。

        可扩展到上万条候选：不逐条调用 Embedding API，而是批量 reconstruct + 批量
        Faiss 搜索，内存峰值由 vector_retriever 的分块控制。
        """
        threshold = float(cfg.get("semantic_similarity_threshold", 0.7))
        candidate_ids = [mem["id"] for mem in candidates]

        pairs: list[tuple[int, int, float]] = []
        try:
            vector_retriever = getattr(self.memory_engine, "vector_retriever", None)
            if vector_retriever is None:
                return self._group_by_session(candidates)
            pairs = await vector_retriever.find_similar_pairs(
                candidate_ids, threshold
            )
        except Exception as e:
            logger.warning(f"[记忆整合] 语义聚类失败，回退到同会话聚合: {e}")
            return self._group_by_session(candidates)

        if not pairs:
            return []

        parent: dict[int, int] = {mem["id"]: mem["id"] for mem in candidates}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for a, b, _sim in pairs:
            union(a, b)

        groups: dict[int, list[dict[str, Any]]] = {}
        for mem in candidates:
            root = find(mem["id"])
            groups.setdefault(root, []).append(mem)
        return list(groups.values())

    async def _consolidate_group(
        self, group: list[dict[str, Any]], cfg: dict[str, Any]
    ) -> dict[str, Any]:
        merged = await self.memory_processor.merge_memories(group)

        summary = merged["summary"]
        key_facts = merged["key_facts"]
        rich_content = summary
        if key_facts:
            rich_content = f"{summary} | {'；'.join(key_facts)}"

        session_id = None
        persona_id = None
        if cfg.get("granularity", "session") == "session":
            session_id = group[0]["metadata"].get("session_id")
            persona_id = group[0]["metadata"].get("persona_id")

        metadata = {
            "topics": merged["topics"],
            "key_facts": key_facts,
            "persona_summary": summary,
            "canonical_summary": rich_content,
            "summary_schema_version": "v2",
            "consolidated_from": [mem["id"] for mem in group],
            "consolidated_at": time.time(),
        }

        new_id = await self.memory_engine.add_memory(
            content=rich_content,
            session_id=session_id,
            persona_id=persona_id,
            importance=merged["importance"],
            metadata=metadata,
        )

        old_ids = [mem["id"] for mem in group]
        if cfg.get("keep_original", "archive") == "archive":
            removed = await self.memory_engine.archive_memories(old_ids)
        else:
            removed = await self.memory_engine.batch_delete_memories(old_ids)

        logger.info(
            f"[记忆整合] 整合 {len(group)} 条记忆 -> 新记忆 {new_id}，"
            f"{cfg.get('keep_original', 'archive')} {removed} 条旧记忆"
        )
        return {"new_id": new_id, "merged": len(group), "removed": removed}


__all__ = ["MemoryConsolidationManager"]
