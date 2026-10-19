"""供 Agent 主动调用的长期记忆遗忘工具（管理员专用，可逆归档）。

决策（issue #279）：模型只能做软删除（归档），不做物理删除；归档后的记忆
仍保留在 documents 表中，管理员可从 WebUI 记忆页把状态改回 active 恢复。
"""

import asyncio
import json
import math
import time
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .agentic_recall_common import resolve_tool_access


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _document_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    """读取记忆文档的 metadata（兼容 JSON 字符串形式）。"""
    metadata = memory.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return {}
    return metadata if isinstance(metadata, dict) else {}


@dataclass
class MemoryForgetTool(FunctionTool[AstrAgentContext]):
    """长期记忆遗忘工具：仅归档（软删除），可由管理员恢复。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    context: Any = None
    config_manager: Any = None
    memory_engine: Any = None
    # 会话 ID -> 上次成功归档时间戳（实例级限流状态，#279）
    rate_limit_state: dict[str, float] = field(default_factory=dict)

    name: str = "forget_long_term_memory"
    description: str = (
        "Forget long-term memories that are wrong, outdated, or sensitive. "
        "Use ONLY memory ids returned by recall tools in this conversation; never guess ids "
        "and never bulk-delete. The deletion is a reversible soft delete (archive): the memory "
        "is removed from retrieval but an admin can restore it from the WebUI memory page. "
        "Requires admin permission."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "memory_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Memory ids to archive (soft-delete). Use only ids returned by recall tools in this conversation; never guess ids.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional short reason why these memories should be forgotten (stored in audit metadata).",
                    "default": "",
                },
            },
            "required": ["memory_ids"],
        }
    )

    def _max_memories(self) -> int:
        """单次调用允许归档的最大条数（配置异常时回退默认值并夹紧范围）。"""
        try:
            value = int(
                self.config_manager.get("agent_tools.forget_tool_max_memories", 3)
            )
        except (TypeError, ValueError):
            value = 3
        return max(1, min(10, value))

    def _cooldown_seconds(self) -> int:
        """同一会话两次成功归档之间的最小间隔（秒，0 表示不限制）。"""
        try:
            value = int(
                self.config_manager.get(
                    "agent_tools.forget_tool_cooldown_seconds", 60
                )
            )
        except (TypeError, ValueError):
            value = 60
        return max(0, min(3600, value))

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        memory_ids: list[int],
        reason: str = "",
    ) -> ToolExecResult:
        """执行长期记忆归档（软删除）。"""
        if (
            self.context is None
            or self.config_manager is None
            or self.memory_engine is None
        ):
            return _json_result(
                {"forgotten": False, "error": "memory forget tool is not initialized"}
            )

        try:
            event, recall_session_id, recall_persona_id, access_error = (
                await resolve_tool_access(self.config_manager, self.context, context)
            )
            if access_error is not None:
                return _json_result({"forgotten": False, **access_error})

            # 管理员闸门：遗忘属于破坏性操作，非管理员一律拒绝
            is_admin = getattr(event, "is_admin", None)
            try:
                admin_allowed = bool(is_admin()) if callable(is_admin) else False
            except Exception:
                admin_allowed = False
            if not admin_allowed:
                return _json_result(
                    {"forgotten": False, "error": "admin permission required"}
                )

            # 参数校验：必须是非空整数列表，去重后不超过配置上限
            if not isinstance(memory_ids, list) or not memory_ids:
                return _json_result(
                    {
                        "forgotten": False,
                        "error": "memory_ids must be a non-empty list of integers",
                    }
                )
            unique_ids: list[int] = []
            for item in memory_ids:
                if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
                    return _json_result(
                        {
                            "forgotten": False,
                            "error": "memory_ids must be a non-empty list of integers",
                        }
                    )
                if item not in unique_ids:
                    unique_ids.append(item)
            max_memories = self._max_memories()
            if len(unique_ids) > max_memories:
                return _json_result(
                    {
                        "forgotten": False,
                        "error": "too many memory_ids",
                        "max_memories": max_memories,
                    }
                )

            # 限流：同一会话冷却期内拒绝，且只在归档成功后才刷新时间戳
            cooldown = self._cooldown_seconds()
            now = time.time()
            if cooldown > 0 and recall_session_id is not None:
                last_archive_ts = self.rate_limit_state.get(recall_session_id)
                if last_archive_ts is not None and now - last_archive_ts < cooldown:
                    retry_after = max(
                        1, int(math.ceil(cooldown - (now - last_archive_ts)))
                    )
                    return _json_result(
                        {
                            "forgotten": False,
                            "error": "rate limited",
                            "retry_after_seconds": retry_after,
                        }
                    )

            # 作用域校验（与检索可见性一致）：任一 id 不合法则整批拒绝，绝不部分归档
            invalid_ids: list[int] = []
            for memory_id in unique_ids:
                memory = await self.memory_engine.get_memory(memory_id)
                if not memory:
                    invalid_ids.append(memory_id)
                    continue
                metadata = _document_metadata(memory)
                if str(metadata.get("status") or "active") != "active":
                    invalid_ids.append(memory_id)
                    continue
                if metadata.get("session_id") != recall_session_id:
                    invalid_ids.append(memory_id)
                    continue
                if (
                    recall_persona_id is not None
                    and metadata.get("persona_id") != recall_persona_id
                ):
                    invalid_ids.append(memory_id)
                    continue
            if invalid_ids:
                return _json_result(
                    {
                        "forgotten": False,
                        "error": "some memory ids are not visible in this scope",
                        "invalid_ids": invalid_ids,
                    }
                )

            cleaned_reason = (reason or "").strip() if isinstance(reason, str) else ""
            archived_count = await self.memory_engine.archive_memories(
                unique_ids,
                audit={
                    "archived_by": "llm_tool",
                    "archive_reason": cleaned_reason,
                    "archive_session_id": recall_session_id,
                },
            )
            if not archived_count:
                return _json_result(
                    {
                        "forgotten": False,
                        "error": "no active memories were archived",
                    }
                )

            if recall_session_id is not None:
                self.rate_limit_state[recall_session_id] = time.time()
            return _json_result(
                {
                    "forgotten": True,
                    "memory_ids": unique_ids,
                    "archived_count": archived_count,
                    "reversible": True,
                    "note": (
                        "Archived (soft-deleted). An admin can restore them from the "
                        "WebUI memory page (status filter: archived -> set active)."
                    ),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"记忆工具遗忘失败: {e}", exc_info=True)
            return _json_result({"forgotten": False, "error": "internal_error"})
