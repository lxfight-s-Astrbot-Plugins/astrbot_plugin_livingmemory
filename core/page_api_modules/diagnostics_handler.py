"""Diagnostics Page API: LLM usage and channel memory overview.

Read-only endpoints backing the dashboard diagnostics page. Usage is the
process-local snapshot; channel rows are aggregated from the active memory pool.
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..base.usage_metrics import get_usage_snapshot
from ..memory_scope import CHANNEL_SCOPE_PREFIX, adapter_sql

_CHANNEL_LIKE = f"{CHANNEL_SCOPE_PREFIX}:%"
_MAX_CHANNELS = 200


class DiagnosticsHandler:
    """Aggregated diagnostics for usage and channel memories."""

    def __init__(self, utils) -> None:
        """初始化处理器。

        Args:
            utils: Page API 工具实例。
        """
        self.utils = utils

    @staticmethod
    def usage(config_manager) -> dict[str, Any]:
        """Return the process-local LLM usage snapshot and fallback settings."""
        fallback_ids: list[str] = []
        timeout_seconds = 60
        if config_manager is not None:
            raw = config_manager.get("provider_settings.llm_fallback_provider_ids", [])
            if isinstance(raw, list):
                fallback_ids = [str(item).strip() for item in raw if str(item).strip()]
            try:
                timeout_seconds = int(
                    config_manager.get("provider_settings.llm_call_timeout_seconds", 60)
                )
            except (TypeError, ValueError):
                timeout_seconds = 60
        return {
            "usage": get_usage_snapshot(),
            "fallback_provider_ids": fallback_ids,
            "timeout_seconds": timeout_seconds,
        }

    async def channels(self, memory_engine) -> dict[str, Any]:
        """List channel scopes with active memory counts and latest write time."""
        predicate, params = adapter_sql("metadata")
        sql = f"""
            SELECT
                json_extract(metadata, '$.session_id') AS scope,
                COUNT(*) AS memory_count,
                MAX(json_extract(metadata, '$.create_time')) AS latest_create_time
            FROM documents
            WHERE json_extract(metadata, '$.session_id') LIKE ?
              AND json_extract(metadata, '$.status') = 'active'
              AND {predicate}
            GROUP BY scope
            ORDER BY latest_create_time DESC, memory_count DESC
            LIMIT ?
        """
        try:
            cursor = await memory_engine.db_connection.execute(
                sql, [_CHANNEL_LIKE, *params, _MAX_CHANNELS]
            )
            rows = await cursor.fetchall()
            await cursor.close()
        except Exception as exc:
            logger.error(f"[PageAPI] 读取频道记忆失败: {exc}", exc_info=True)
            return self.utils.error("读取频道记忆失败")

        channels: list[dict[str, Any]] = []
        for row in rows:
            scope = str(row["scope"] or "")
            rest = scope[len(CHANNEL_SCOPE_PREFIX) + 1 :]
            platform, _, group_id = rest.partition(":")
            channels.append(
                {
                    "scope": scope,
                    "platform": platform or None,
                    "channel_id": group_id or None,
                    "memory_count": int(row["memory_count"] or 0),
                    "latest_create_time": row["latest_create_time"],
                }
            )
        return self.utils.ok({"channels": channels, "total": len(channels)})
