"""Session catalog handler for the dashboard's layered session picker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

if TYPE_CHECKING:
    from ..managers.conversation_manager import ConversationManager
    from .utils import PageApiUtils


class SessionHandler:
    """Provide a lightweight, filterable catalog of recorded sessions."""

    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    @staticmethod
    def _parse_session_id(session_id: str) -> dict[str, str]:
        parts = str(session_id or "").split(":", 2)
        platform_id = parts[0] if parts else ""
        message_type = parts[1] if len(parts) > 1 else ""
        target_id = parts[2] if len(parts) > 2 else str(session_id or "")
        normalized_type = message_type.casefold()
        if "group" in normalized_type:
            chat_type = "group"
        elif "friend" in normalized_type or "private" in normalized_type:
            chat_type = "private"
        else:
            chat_type = "other"
        return {
            "platform_id": platform_id,
            "message_type": message_type,
            "chat_type": chat_type,
            "target_id": target_id,
        }

    @staticmethod
    def _optional_timestamp(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("更新时间范围必须是 Unix 时间戳") from None

    async def list_sessions(
        self,
        conversation_manager: "ConversationManager | None",
    ) -> dict[str, Any]:
        """List recent sessions and facets used by the layered picker."""
        if conversation_manager is None:
            return self.utils.ok(
                {"items": [], "facets": {"platform_ids": [], "chat_types": []}}
            )

        args = request.args
        platform_id = self.utils.optional_text(args.get("platform_id"))
        chat_type = self.utils.optional_text(args.get("chat_type"))
        target_query = str(args.get("target_query", "")).strip().casefold()
        try:
            updated_after = self._optional_timestamp(args.get("updated_after"))
            updated_before = self._optional_timestamp(args.get("updated_before"))
            limit = max(1, min(int(args.get("limit", 200)), 500))
        except (TypeError, ValueError) as exc:
            return self.utils.error(str(exc) or "会话筛选参数无效")

        if (
            updated_after is not None
            and updated_before is not None
            and updated_after > updated_before
        ):
            return self.utils.error("更新时间范围起点不能晚于终点")
        if chat_type and chat_type not in {"group", "private", "other"}:
            return self.utils.error("chat_type 必须是 group、private 或 other")

        try:
            # Only the compact sessions table is read; message and memory bodies
            # are never scanned for picker filtering.
            sessions = await conversation_manager.store.get_recent_sessions(limit=5000)
            parsed_rows: list[dict[str, Any]] = []
            for session in sessions:
                parsed = self._parse_session_id(session.session_id)
                parsed_rows.append(
                    {
                        "session_id": session.session_id,
                        "platform": session.platform,
                        "platform_id": parsed["platform_id"],
                        "message_type": parsed["message_type"],
                        "chat_type": parsed["chat_type"],
                        "target_id": parsed["target_id"],
                        "created_at": session.created_at,
                        "last_active_at": session.last_active_at,
                        "message_count": session.message_count,
                    }
                )

            platform_ids = sorted(
                {row["platform_id"] for row in parsed_rows if row["platform_id"]},
                key=str.casefold,
            )
            account_rows = [
                row
                for row in parsed_rows
                if not platform_id or row["platform_id"] == platform_id
            ]
            chat_types = sorted({row["chat_type"] for row in account_rows})

            # Layered mode deliberately stops here until both mandatory levels
            # are selected. This avoids preparing a large target dropdown while
            # the user is still choosing an account or chat type.
            if not platform_id or not chat_type:
                return self.utils.ok(
                    {
                        "items": [],
                        "total": 0,
                        "facets": {
                            "platform_ids": platform_ids,
                            "chat_types": chat_types,
                        },
                        "applied_filters": {
                            "platform_id": platform_id,
                            "chat_type": chat_type,
                            "updated_after": updated_after,
                            "updated_before": updated_before,
                            "target_query": target_query,
                        },
                    }
                )

            filtered: list[dict[str, Any]] = []
            for row in account_rows:
                if chat_type and row["chat_type"] != chat_type:
                    continue
                last_active = float(row["last_active_at"] or 0.0)
                if updated_after is not None and last_active < updated_after:
                    continue
                if updated_before is not None and last_active > updated_before:
                    continue
                if target_query and target_query not in str(row["target_id"]).casefold():
                    continue
                filtered.append(row)

            visible = filtered[:limit]
            return self.utils.ok(
                {
                    "items": visible,
                    "total": len(filtered),
                    "facets": {
                        "platform_ids": platform_ids,
                        "chat_types": chat_types,
                    },
                    "applied_filters": {
                        "platform_id": platform_id,
                        "chat_type": chat_type,
                        "updated_after": updated_after,
                        "updated_before": updated_before,
                        "target_query": target_query,
                    },
                }
            )
        except Exception as exc:
            logger.error(f"[PageAPI] 获取会话目录失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))


__all__ = ["SessionHandler"]
