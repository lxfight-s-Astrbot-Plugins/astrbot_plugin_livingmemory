"""
记忆召回模块
负责长期记忆的检索和注入
"""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

from ..utils import (
    OperationContext,
    format_memories_for_fake_tool_call,
    format_memories_for_injection,
    get_persona_id,
)

if TYPE_CHECKING:
    from ..base.config_manager import ConfigManager
    from ..managers.conversation_manager import ConversationManager
    from ..managers.memory_engine import MemoryEngine
    from ..utils.injection_adapter import InjectionAdapter
    from .message_utils import MessageUtils


class MemoryRecall:
    """记忆召回类"""

    def __init__(
        self,
        context,
        config_manager: "ConfigManager",
        memory_engine: "MemoryEngine",
        conversation_manager: "ConversationManager",
        message_utils: "MessageUtils",
        injection_adapter: "InjectionAdapter",
    ):
        """
        初始化记忆召回模块

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            message_utils: 消息处理工具
            injection_adapter: 注入适配器
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.message_utils = message_utils
        self.injection_adapter = injection_adapter

    async def handle_memory_recall(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """Query and inject long-term memory before LLM request"""
        try:
            session_id = event.unified_msg_origin
            logger.debug(f"[DEBUG-Recall] 获取到 unified_msg_origin: {session_id}")

            # 检测异常session_id
            if session_id and (
                "Error:" in session_id or "error:" in session_id.lower()
            ):
                logger.warning(
                    f"[{session_id}] 检测到异常的session_id，这可能导致记忆功能异常。"
                )

            async with OperationContext("记忆召回", session_id):
                prompt_text = getattr(req, "prompt", "")
                extra_parts = getattr(req, "extra_user_content_parts", [])
                has_prompt_text = isinstance(prompt_text, str) and bool(
                    prompt_text.strip()
                )
                has_extra_parts = bool(extra_parts)

                if not has_prompt_text and not has_extra_parts:
                    logger.debug(f"[{session_id}] 请求中无可用用户内容，跳过记忆召回")
                    return

                normalized = self._normalize_text_only_context_parts(req, session_id)
                if normalized > 0:
                    logger.info(f"[{session_id}] 已归一化 {normalized} 条纯文本历史消息")

                # 自动删除旧的注入记忆
                if self.config_manager.get("recall_engine.auto_remove_injected", True):
                    removed = self._remove_injected_memories_from_context(
                        req, session_id
                    )
                    removed += self._remove_fake_tool_call_from_context(req, session_id)
                    if removed > 0:
                        logger.info(
                            f"[{session_id}] 已清理 {removed} 处历史记忆注入片段"
                        )

                # 先提取用户消息（消息存储和召回都需要）
                actual_query = await self.message_utils.get_event_message_str(event)

                request_query = (
                    prompt_text.strip() if isinstance(prompt_text, str) else ""
                )

                # 存储用户消息（仅私聊），无论是否启用召回都需要
                is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
                if not is_group and actual_query:
                    message_to_store = request_query
                    if not message_to_store:
                        message_to_store = (
                            await self.message_utils.extract_message_content(event, req)
                        )
                    if not message_to_store:
                        message_to_store = actual_query.strip()
                    await self.conversation_manager.add_message_from_event(
                        event=event,
                        role="user",
                        content=message_to_store,
                    )
                    await self.message_utils.enforce_message_limit(session_id)

                # 若 top_k <= 0，跳过记忆检索和注入，但上述清理和消息存储已执行
                top_k = self.config_manager.get("recall_engine.top_k", 5)
                if top_k <= 0:
                    logger.info(
                        f"[{session_id}] top_k={top_k} <= 0，跳过记忆检索和注入"
                    )
                    return

                if not actual_query:
                    logger.warning(f"[{session_id}] 原始用户消息为空，跳过记忆召回")
                    return

                # 获取过滤配置
                filtering_config = self.config_manager.filtering_settings
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                # 获取 persona_id，与 AstrBot 主流程保持一致的三级优先级：
                # 1. session_service_config（最高）
                # 2. req.conversation.persona_id（会话级）
                # 3. 全局默认人格（最低）
                # 注意：on_llm_request 钩子在 _ensure_persona_and_skills 之前触发，
                # 因此不能直接依赖 req.system_prompt 已注入人格，需自行走完整优先级。
                persona_id = await get_persona_id(self.context, event)

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                # 使用原始用户输入作为召回关键字
                query_for_search = actual_query

                # 上下文扩展：拼接最近2轮对话作为查询，提升检索精准度
                if self.config_manager.get(
                    "recall_engine.inject_with_recent_context", False
                ):
                    try:
                        recent_messages = (
                            await self.conversation_manager.get_context(
                                session_id, max_messages=5
                            )
                        )
                        if recent_messages and len(recent_messages) > 1:
                            # recent_messages 按 timestamp DESC 排列（最新在前）
                            # 跳过索引0（当前消息），取后续消息作为扩展上下文
                            context_parts = []
                            for msg in reversed(recent_messages[1:]):
                                content = msg.get("content", "")
                                if content and content.strip():
                                    context_parts.append(content.strip())
                            if context_parts:
                                expanded = " | ".join(context_parts)
                                query_for_search = expanded + " " + actual_query
                                logger.info(
                                    f"[{session_id}] 上下文扩展查询: "
                                    f"{len(context_parts)}条历史消息 + 当前消息"
                                )
                    except Exception as e:
                        logger.warning(f"[{session_id}] 获取上下文扩展失败: {e}")

                # 执行记忆召回
                logger.info(
                    f"[{session_id}] 开始记忆召回，查询='{query_for_search[:80]}...'"
                )

                recalled_memories = await self.memory_engine.search_memories(
                    query=query_for_search,
                    k=self.config_manager.get("recall_engine.top_k", 5),
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                )

                if recalled_memories:
                    logger.info(
                        f"[{session_id}] 检索到 {len(recalled_memories)} 条记忆"
                    )

                    # 格式化并注入记忆
                    memory_list = [
                        {
                            "id": getattr(mem, "doc_id", None),
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": mem.metadata,
                            "timestamp": mem.metadata.get("create_time"),
                        }
                        for mem in recalled_memories
                    ]

                    # 输出详细记忆信息
                    for i, mem in enumerate(recalled_memories, 1):
                        logger.debug(
                            f"[{session_id}] 记忆 #{i}: 得分={mem.final_score:.3f}, "
                            f"重要性={mem.metadata.get('importance', 0.5):.2f}, "
                            f"内容={mem.content[:100]}..."
                        )

                    # 根据配置选择注入方式（含 Provider 兼容降级）
                    configured_method = self.config_manager.get(
                        "recall_engine.injection_method", "extra_user_content"
                    )
                    provider = None
                    if configured_method in (
                        "fake_tool_call",
                        "fake_tool_call_deepseek_v4",
                    ):
                        try:
                            provider = self.context.get_using_provider(session_id)
                        except Exception as e:
                            logger.warning(
                                f"[{session_id}] 获取当前 Provider 失败，"
                                f"将按无 Provider 继续解析注入模式: {e}"
                            )
                    injection_method, fallback_reason = (
                        self.injection_adapter.resolve(provider, configured_method)
                    )
                    if fallback_reason:
                        logger.warning(
                            f"[{session_id}] 注入模式从 {configured_method} 降级为 "
                            f"{injection_method}: {fallback_reason}"
                        )

                    memory_str = format_memories_for_injection(memory_list)

                    if injection_method == "user_message_before":
                        req.prompt = memory_str + "\n\n" + (req.prompt or "")
                        logger.info(
                            f"[{session_id}] 成功向用户消息前注入 {len(recalled_memories)} 条记忆"
                        )
                    elif injection_method == "user_message_after":
                        req.prompt = (req.prompt or "") + "\n\n" + memory_str
                        logger.info(
                            f"[{session_id}] 成功向用户消息后注入 {len(recalled_memories)} 条记忆"
                        )
                    elif injection_method == "fake_tool_call":
                        fake_messages = format_memories_for_fake_tool_call(
                            memory_list,
                            query=actual_query,
                            k=self.config_manager.get("recall_engine.top_k", 5),
                            session_filtered=use_session_filtering,
                            persona_filtered=use_persona_filtering,
                        )
                        if fake_messages:
                            req.contexts.extend(fake_messages)
                            logger.info(
                                f"[{session_id}] 成功以伪造工具调用方式注入 "
                                f"{len(recalled_memories)} 条记忆"
                            )
                    else:
                        # extra_user_content（推荐）：追加到用户消息末尾，
                        # 不影响前缀缓存且 mark_as_temp 后不污染对话历史
                        req.extra_user_content_parts.append(
                            TextPart(text=memory_str).mark_as_temp()
                        )
                        logger.info(
                            f"[{session_id}] 成功向用户消息末尾注入 "
                            f"{len(recalled_memories)} 条记忆"
                        )
                else:
                    logger.info(f"[{session_id}] 未找到相关记忆")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)

    def _remove_injected_memories_from_context(
        self, req: ProviderRequest, session_id: str
    ) -> int:
        """从请求上下文中移除临时注入的记忆片段"""
        import re
        from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

        removed = 0

        # 清理 system_prompt（兼容旧版本注入残留）
        if hasattr(req, "system_prompt") and req.system_prompt:
            if isinstance(req.system_prompt, str):
                original_prompt = req.system_prompt
                if (
                    MEMORY_INJECTION_HEADER in original_prompt
                    and MEMORY_INJECTION_FOOTER in original_prompt
                ):
                    # 使用正则清理记忆片段
                    pattern = re.compile(
                        re.escape(MEMORY_INJECTION_HEADER)
                        + r".*?"
                        + re.escape(MEMORY_INJECTION_FOOTER),
                        re.DOTALL,
                    )
                    cleaned_prompt = pattern.sub("", original_prompt)
                    cleaned_prompt = re.sub(r"\n{3,}", "\n\n", cleaned_prompt).strip()
                    req.system_prompt = cleaned_prompt
                    if cleaned_prompt != original_prompt:
                        removed += 1

        # 清理 extra_user_content_parts（通过 mark_as_temp/_no_save 标记）
        parts_before = len(getattr(req, "extra_user_content_parts", []))
        if parts_before > 0:
            req.extra_user_content_parts = [
                part
                for part in req.extra_user_content_parts
                if not self._is_livingmemory_temp_part(part)
            ]
            parts_after = len(req.extra_user_content_parts)
            removed += parts_before - parts_after

        return removed

    def _is_livingmemory_temp_part(self, part) -> bool:
        """判断是否为 LivingMemory 本轮临时注入的 extra_user_content part"""
        from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

        text = getattr(part, "text", "")
        return (
            getattr(part, "_no_save", False)
            and isinstance(text, str)
            and MEMORY_INJECTION_HEADER in text
            and MEMORY_INJECTION_FOOTER in text
        )

    def _normalize_text_only_context_parts(
        self, req: ProviderRequest, session_id: str
    ) -> int:
        """把历史中的纯文本 content parts 折叠回字符串，避免污染长期上下文格式"""
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list):
            return 0

        normalized = 0
        for msg in contexts:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list) or not content:
                continue

            text_parts = []
            text_only = True
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    text_only = False
                    break
                text_parts.append(str(part.get("text", "") or ""))

            if not text_only:
                continue

            msg["content"] = "".join(text_parts)
            normalized += 1

        if normalized:
            logger.debug(f"[{session_id}] 已归一化 {normalized} 条纯文本历史 content parts")
        return normalized

    def _remove_fake_tool_call_from_context(
        self, req: ProviderRequest, session_id: str
    ) -> int:
        """从请求上下文中移除伪造的工具调用记忆（fake_tool_call 注入方式）

        识别并移除以 FAKE_TOOL_CALL_ID_PREFIX 为 ID 前缀的
        assistant(tool_calls) + tool(result) 消息对。
        """
        from ..base.constants import FAKE_TOOL_CALL_ID_PREFIX

        if not hasattr(req, "contexts") or not req.contexts:
            return 0

        removed = 0
        indices_to_remove: set[int] = set()
        fake_call_ids: set[str] = set()

        try:
            # 单轮扫描：同时收集伪造 assistant(tool_calls) 和对应 tool(result) 消息
            for i, msg in enumerate(req.contexts):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        tc_id = (
                            tc.get("id", "")
                            if isinstance(tc, dict)
                            else getattr(tc, "id", "")
                        )
                        if tc_id.startswith(FAKE_TOOL_CALL_ID_PREFIX):
                            fake_call_ids.add(tc_id)
                            indices_to_remove.add(i)
                elif role == "tool":
                    tc_id = msg.get("tool_call_id", "")
                    if tc_id in fake_call_ids:
                        indices_to_remove.add(i)

            # 从后往前删除，避免索引偏移
            for i in sorted(indices_to_remove, reverse=True):
                req.contexts.pop(i)
                removed += 1

        except Exception:
            pass

        return removed
