"""供 Agent 主动调用的长期记忆检索工具。"""

from dataclasses import field
import json
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..base.config_manager import ConfigManager
from ..utils import get_persona_id


def _json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str)


@dataclass
class MemorySearchTool(FunctionTool[AstrAgentContext]):
    """长期记忆主动检索工具。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    context: Any = None
    config_manager: ConfigManager | None = None
    memory_engine: Any = None

    name: str = "search_long_term_memory"
    description: str = (
        "Search long-term memory when the current context is insufficient. "
        "Use concise, focused search keywords instead of copying the full user message. "
        "Call this when the user asks you to recall prior facts, preferences, agreements, or older context, "
        "or when resolving ambiguous references requires checking memory. "
        "Prefer short topic phrases, named entities, preferences, commitments, or past events as the query. "
        "If the first search is not enough, refine the query and search again."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise search query for long-term memory. Prefer key entities, topics, preferences, commitments, or past events instead of copying the full user message.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of memory items to return. Keep this small unless more evidence is needed.",
                    "default": 5,
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str,
        k: int = 5,
    ) -> ToolExecResult:
        """执行长期记忆检索。"""
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return _json_result(
                {
                    "query": "",
                    "count": 0,
                    "results": [],
                    "error": "query is empty",
                }
            )

        if (
            self.config_manager is None
            or self.memory_engine is None
            or self.context is None
        ):
            return _json_result(
                {
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": "memory search tool is not initialized",
                }
            )

        try:
            event = context.context.event
            filtering_config = self.config_manager.filtering_settings
            use_persona_filtering = filtering_config.get("use_persona_filtering", True)
            use_session_filtering = filtering_config.get("use_session_filtering", True)

            session_id = event.unified_msg_origin
            persona_id = await get_persona_id(self.context, event)

            recall_session_id = session_id if use_session_filtering else None
            recall_persona_id = persona_id if use_persona_filtering else None

            default_k = int(self.config_manager.get("recall_engine.top_k", 5))
            max_k = int(self.config_manager.get("recall_engine.max_k", 10))
            requested_k = default_k if k is None else k
            limited_k = max(1, min(int(requested_k), max_k))

            memories = await self.memory_engine.search_memories(
                query=cleaned_query,
                k=limited_k,
                session_id=recall_session_id,
                persona_id=recall_persona_id,
            )

            serialized_results = []
            for memory in memories:
                metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
                serialized_results.append(
                    {
                        "id": memory.doc_id,
                        "content": memory.content,
                        "score": memory.final_score,
                        "importance": metadata.get("importance"),
                        "session_id": metadata.get("session_id"),
                        "persona_id": metadata.get("persona_id"),
                        "create_time": metadata.get("create_time"),
                        "last_access_time": metadata.get("last_access_time"),
                    }
                )

            return _json_result(
                {
                    "query": cleaned_query,
                    "applied_filters": {
                        "session_filtered": use_session_filtering,
                        "persona_filtered": use_persona_filtering,
                    },
                    "count": len(serialized_results),
                    "results": serialized_results,
                }
            )
        except Exception as e:
            logger.error(f"记忆工具检索失败: {e}", exc_info=True)
            return _json_result(
                {
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": "internal_error",
                }
            )
