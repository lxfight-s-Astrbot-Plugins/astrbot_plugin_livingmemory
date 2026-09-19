"""Beta 自主多轮回忆的增强搜索工具。

仅在 agent_tools.enable_agentic_recall_beta 开启时注册；工具名保持
recall_long_term_memory，与基线工具在注册时二选一，不会并存。
旧参数组合（query/k/include_source）在 Beta 下继续可用。
"""

import asyncio
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..base.config_manager import ConfigManager
from .agentic_recall_budget import (
    get_or_create_budget,
    run_within_budget,
    try_consume_call,
)
from .agentic_recall_common import (
    bounded_status,
    derive_speaker_identity,
    parse_time_bound,
    resolve_tool_access,
)
from .memory_evidence_reader import read_memory_evidence_core
from .memory_search_output import format_search_result


@dataclass
class AgenticMemorySearchTool(FunctionTool[AstrAgentContext]):
    """Beta 自主回忆搜索工具：支持按当前发言人起步、时间过滤、续查。"""

    __pydantic_config__ = {"arbitrary_types_allowed": True}

    context: Any = None
    config_manager: ConfigManager | None = None
    memory_engine: Any = None

    name: str = "recall_long_term_memory"
    description: str = (
        "Recall long-term memory when the current context is insufficient, and keep "
        "investigating within the same reply when evidence is incomplete. "
        "Use target='current_speaker' to browse what you remember about the current "
        "speaker, e.g. when they ask 'do you still remember me'; an empty query lists "
        "their experiences by event time, and a non-empty query narrows them by topic. "
        "Use target='conversation' with concise topic keywords for events, preferences, "
        "agreements, or ambiguous references. "
        "After any recall you may deep-read a returned memory_id with the read tool "
        "(read_memory_evidence) when it is available in this request, for exact facts "
        "or retained original messages. "
        "To continue searching, pass exclude_ids with already-seen ids, narrow with "
        "start_time/end_time, or pass next_cursor as search_offset "
        "to page further through a speaker's history. Stop when evidence is sufficient, "
        "no new leads appear, or the tool reports the budget is used up; never assume "
        "an empty result means it never happened."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Concise recall keywords (entities, topics, preferences, "
                        "commitments, past events). Required when target='conversation'. "
                        "With target='current_speaker' it narrows the speaker's "
                        "memories by topic and may be empty to list them all."
                    ),
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum memory items to return for one recall. Keep small.",
                    "default": 5,
                },
                "include_source": {
                    "type": "boolean",
                    "description": (
                        "Also read retained original messages for returned items (permission and "
                        "lifecycle checked again per item). Use only when exact wording matters."
                    ),
                    "default": False,
                },
                "target": {
                    "type": "string",
                    "enum": ["conversation", "current_speaker"],
                    "description": (
                        "conversation: keyword/vector/graph search about a topic. "
                        "current_speaker: browse memories about the current sender; results are "
                        "ordered by event time and identity confidence is labeled per item."
                    ),
                    "default": "conversation",
                },
                "start_time": {
                    "type": "string",
                    "description": (
                        "Optional ISO date/datetime lower bound on when the remembered event "
                        "happened (source time, not write time)."
                    ),
                },
                "end_time": {
                    "type": "string",
                    "description": "Optional ISO date/datetime upper bound on event source time.",
                },
                "exclude_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Memory ids already seen in this conversation; they are filtered out "
                        "before choosing results so follow-up searches return new evidence."
                    ),
                },
                "search_offset": {
                    "type": "string",
                    "description": (
                        "For target='current_speaker' only: pass the next_cursor value from "
                        "the previous result to continue browsing older experiences."
                    ),
                    "default": "",
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str = "",
        k: int = 5,
        include_source: bool = False,
        target: str = "conversation",
        start_time: str = "",
        end_time: str = "",
        exclude_ids: list[int] | None = None,
        search_offset: str = "",
    ) -> ToolExecResult:
        """执行 Beta 自主回忆检索。"""
        if (
            self.config_manager is None
            or self.memory_engine is None
            or self.context is None
        ):
            return bounded_status(
                {
                    "query": query or "",
                    "count": 0,
                    "results": [],
                    "status": "error",
                    "error": "memory search tool is not initialized",
                }
            )

        (
            event,
            recall_session_id,
            recall_persona_id,
            access_error,
        ) = await resolve_tool_access(self.config_manager, self.context, context)
        if access_error is not None:
            return bounded_status(
                {
                    "query": query or "",
                    "count": 0,
                    "results": [],
                    "status": "error",
                    **access_error,
                }
            )

        budget = get_or_create_budget(event, self.config_manager)
        if not try_consume_call(budget):
            return bounded_status(
                {
                    "query": query or "",
                    "count": 0,
                    "results": [],
                    "status": "budget_exhausted",
                    "message": "memory tool call budget is used up; answer with evidence already gathered",
                    "remaining_budget": budget.to_status(),
                }
            )

        cleaned_query = (query or "").strip()
        normalized_target = target or "conversation"
        if normalized_target not in ("conversation", "current_speaker"):
            return bounded_status(
                {
                    "status": "invalid_query",
                    "error": "unsupported target",
                    "count": 0,
                    "results": [],
                    "remaining_budget": budget.to_status(),
                }
            )
        if normalized_target == "conversation" and not cleaned_query:
            return bounded_status(
                {
                    "query": "",
                    "count": 0,
                    "results": [],
                    "status": "invalid_query",
                    "error": "query is empty",
                }
            )

        try:
            start_ts = parse_time_bound(start_time, is_end=False)
            end_ts = parse_time_bound(end_time, is_end=True)
        except ValueError as exc:
            return bounded_status(
                {
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "status": "invalid_query",
                    "error": str(exc),
                }
            )

        identity_key = None
        identity_candidates: list[str] = []
        if normalized_target == "current_speaker":
            identity_key, identity_candidates, identity_status = (
                derive_speaker_identity(self.config_manager, event)
            )
            if identity_status != "ok":
                return bounded_status(
                    {
                        "query": "",
                        "target": normalized_target,
                        "count": 0,
                        "results": [],
                        "status": "identity_unavailable",
                        "message": "current speaker identity is unavailable",
                        "remaining_budget": budget.to_status(),
                    }
                )

        default_k = int(self.config_manager.get("recall_engine.top_k", 5))
        max_k = int(self.config_manager.get("recall_engine.max_k", 10))
        try:
            requested_k = default_k if k is None else int(k)
        except (TypeError, ValueError):
            requested_k = default_k
        limited_k = max(1, min(requested_k, max_k))

        try:
            outcome, _, timed_out = await run_within_budget(
                budget,
                lambda: self.memory_engine.search_memories_agentic(
                    target=normalized_target,
                    query=cleaned_query,
                    k=limited_k,
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                    identity_key=identity_key,
                    identity_candidates=identity_candidates,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    exclude_ids=exclude_ids or [],
                    cursor=search_offset or "",
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return bounded_status(
                {
                    "query": cleaned_query,
                    "target": normalized_target,
                    "count": 0,
                    "results": [],
                    "status": "error",
                    "error": "memory recall failed",
                    "remaining_budget": budget.to_status(),
                }
            )
        if timed_out or outcome is None:
            return bounded_status(
                {
                    "query": cleaned_query,
                    "target": normalized_target,
                    "count": 0,
                    "results": [],
                    "status": "budget_exhausted",
                    "message": "memory tool time budget is used up; answer with evidence already gathered",
                    "remaining_budget": budget.to_status(),
                }
            )

        evidence_by_id: dict[int, dict[str, Any]] = {}

        if include_source and outcome.results:

            async def read_one(memory):
                try:
                    evidence_by_id[memory.doc_id] = await read_memory_evidence_core(
                        memory_engine=self.memory_engine,
                        recall_session_id=recall_session_id,
                        recall_persona_id=recall_persona_id,
                        memory_id=memory.doc_id,
                        include_source=True,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    evidence_by_id[memory.doc_id] = {"status": "error"}

            async def read_sources():
                # 子协程也在预算工厂内创建，超时取消会等待全部子任务收尾。
                await asyncio.gather(*(read_one(memory) for memory in outcome.results))

            _, _, source_timed_out = await run_within_budget(budget, read_sources)
            if source_timed_out:
                for memory in outcome.results:
                    evidence_by_id.setdefault(memory.doc_id, {"status": "timeout"})

        return format_search_result(
            outcome,
            query=cleaned_query,
            target=normalized_target,
            filters={
                "session_filtered": recall_session_id is not None,
                "persona_filtered": recall_persona_id is not None,
                "identity_status": outcome.identity_status,
            },
            budget=budget,
            evidence_by_id=evidence_by_id,
            include_source=include_source,
        )
