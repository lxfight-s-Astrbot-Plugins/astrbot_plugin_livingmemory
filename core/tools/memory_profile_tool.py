"""Agent 主动维护用户档案的工具（update_user_profile）。"""

import asyncio
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..managers.profile_manager import (
    PROFILE_OPERATIONS,
    PROFILE_SECTIONS,
    ProfileManager,
    normalize_profile_updates,
)
from ..memory_scope import (
    adapter_access,
    is_event_memory_allowed,
    resolve_event_memory_access,
)
from ..utils import get_persona_id
from .agentic_recall_common import json_result as _json_result


@dataclass
class UpdateUserProfileTool(FunctionTool[AstrAgentContext]):
    """按分区写入用户档案的 Agent 工具。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    context: Any = None
    config_manager: Any = None
    memory_engine: Any = None

    name: str = "update_user_profile"
    description: str = (
        "Update the structured long-term user profile (basic info, preferences, "
        "current status, task preferences) with a durable fact. Only use when the "
        "user explicitly shares stable profile information; writes go through the "
        "same evidence/confidence rules as automatic summaries and are versioned."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": list(PROFILE_SECTIONS),
                    "description": "Profile section to update.",
                },
                "field": {
                    "type": "string",
                    "description": "Short field name, e.g. '称呼' or '饮食偏好'.",
                },
                "value": {
                    "type": "string",
                    "description": "Value to set/append/remove (max 200 chars).",
                },
                "operation": {
                    "type": "string",
                    "enum": list(PROFILE_OPERATIONS),
                    "description": (
                        "set = replace, append = add list item "
                        "(preferences/task_preferences only), remove = delete item."
                    ),
                    "default": "set",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence 0.0-1.0 for this update.",
                    "default": 0.9,
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Optional long-term memory ids that support the update. "
                        "Without valid evidence the update is stored as a pending candidate."
                    ),
                    "default": [],
                },
            },
            "required": ["section", "field", "value"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        section: str,
        field: str,
        value: str,
        operation: str = "set",
        confidence: float = 0.9,
        evidence: list[int] | None = None,
    ) -> ToolExecResult:
        """执行用户档案写入。"""
        manager = getattr(self.memory_engine, "profile_manager", None)
        if not isinstance(manager, ProfileManager):
            return _json_result(
                {"updated": False, "error": "user profile is not initialized"}
            )
        if not manager.enabled:
            return _json_result({"updated": False, "error": "user profile is disabled"})
        if self.context is None or self.memory_engine is None:
            return _json_result(
                {"updated": False, "error": "user profile tool is not initialized"}
            )

        try:
            event = context.context.event
            with adapter_access(self.config_manager, event, write=True):
                if not is_event_memory_allowed(self.config_manager, event):
                    return _json_result(
                        {"updated": False, "error": "memory access is not allowed"}
                    )
                access = await resolve_event_memory_access(
                    self.config_manager, self.context, event
                )
                if not access.allowed or not access.scope:
                    return _json_result(
                        {"updated": False, "error": "memory scope is unavailable"}
                    )
                persona_id = access.persona_id or await get_persona_id(
                    self.context, event
                )

                updates = normalize_profile_updates(
                    [
                        {
                            "section": section,
                            "field": field,
                            "value": value,
                            "operation": operation,
                            "confidence": confidence,
                            "evidence": evidence or [],
                        }
                    ]
                )
                if not updates:
                    return _json_result(
                        {
                            "updated": False,
                            "error": "invalid section/field/value/operation",
                        }
                    )
                result = await manager.apply_updates(
                    access.scope,
                    persona_id,
                    updates,
                    evidence_memory_ids=list(evidence or []),
                    source="agent_tool",
                )
                return _json_result(
                    {
                        "updated": bool(result.get("applied")),
                        "pending": int(result.get("pending", 0)),
                        "version": result.get("version"),
                        "scope": access.scope,
                        "persona_id": persona_id,
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("用户档案工具写入失败: %s", exc, exc_info=True)
            return _json_result({"updated": False, "error": "internal_error"})


__all__ = ["UpdateUserProfileTool"]
