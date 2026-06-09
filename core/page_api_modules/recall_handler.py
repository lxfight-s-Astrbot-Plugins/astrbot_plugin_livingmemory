"""
召回测试处理模块
"""

import time
from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

if TYPE_CHECKING:
    from .utils import PageApiUtils


class RecallHandler:
    """召回测试处理器"""

    def __init__(self, utils: "PageApiUtils"):
        """
        初始化召回测试处理器

        Args:
            utils: PageApiUtils 工具实例
        """
        self.utils = utils

    async def test_recall(self, memory_engine) -> dict[str, Any]:
        """
        测试记忆召回功能

        Payload:
            - query: 查询文本（必需）
            - k: 返回结果数量（默认5，最大50）
            - session_id: 会话ID过滤（可选）

        Returns:
            包含召回结果和性能指标的字典
        """
        payload = await request.get_json(silent=True) or {}
        query_text = str(payload.get("query", "")).strip()
        if not query_text:
            return self.utils.error("查询内容不能为空")

        try:
            k = min(50, max(1, int(payload.get("k", 5))))
        except (TypeError, ValueError):
            return self.utils.error("k 必须是整数")

        session_id = self.utils.optional_text(payload.get("session_id"))

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
            return self.utils.error(str(exc))

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

        return self.utils.ok(
            {
                "results": formatted_results,
                "total": len(formatted_results),
                "query": query_text,
                "k": k,
                "session_id_filter": session_id,
                "elapsed_time_ms": round(elapsed_time, 2),
            }
        )
