"""
事件处理器
负责处理AstrBot事件钩子
"""

import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from astrbot.api.provider import LLMResponse, ProviderRequest

from .base.config_manager import ConfigManager
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .processors.chatroom_parser import ChatroomContextParser
from .processors.memory_processor import MemoryProcessor
from .utils import (
    OperationContext,
    format_memories_for_injection,
    get_persona_id,
)


class EventHandler:
    """事件处理器"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        memory_processor: MemoryProcessor,
        conversation_manager: ConversationManager,
    ):
        """
        初始化事件处理器

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            memory_processor: 记忆处理器
            conversation_manager: 会话管理器
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.conversation_manager = conversation_manager

        # 消息去重缓存
        self._message_dedup_cache: dict[str, float] = {}
        self._dedup_cache_max_size = 1000
        self._dedup_cache_ttl = 300

    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """捕获所有群聊消息用于记忆存储"""
        # 检查配置
        if not self.config_manager.get(
            "session_manager.enable_full_group_capture", True
        ):
            return

        # 只处理群聊消息
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return

        try:
            session_id = event.unified_msg_origin

            # 检测异常session_id
            if session_id and (
                "Error:" in session_id or "error:" in session_id.lower()
            ):
                logger.warning(
                    f"检测到异常的session_id: {session_id}。"
                    f"这可能是平台适配器初始化问题，建议检查平台配置。"
                )

            message_id = event.message_obj.message_id

            # 消息去重
            if self._is_duplicate_message(message_id):
                logger.debug(f"[{session_id}] 消息已存在,跳过: message_id={message_id}")
                return

            self._mark_message_processed(message_id)

            # 判断是否是Bot自己发送的消息
            is_bot_message = event.get_sender_id() == event.get_self_id()

            # 获取消息内容
            content = self._extract_message_content(event)

            # 确定角色
            role = "assistant" if is_bot_message else "user"

            # 存储消息到数据库
            message = await self.conversation_manager.add_message_from_event(
                event=event,
                role=role,
                content=content,
            )

            # 标记是否为Bot消息
            message.metadata["is_bot_message"] = is_bot_message
            await self._update_message_metadata(message)

            # 执行消息数量上限控制
            await self._enforce_message_limit(session_id)

            logger.debug(
                f"[{session_id}] 捕获群聊消息: "
                f"sender={event.get_sender_name()}({event.get_sender_id()}), "
                f"is_bot={is_bot_message}, content={content[:50]}..."
            )

        except Exception as e:
            logger.error(f"处理群聊全量消息时发生错误: {e}", exc_info=True)

    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """在 LLM 请求前，查询并注入长期记忆"""
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
                # 自动删除旧的注入记忆
                if self.config_manager.get("recall_engine.auto_remove_injected", True):
                    self._remove_injected_memories_from_context(req, session_id)

                # 获取过滤配置
                filtering_config = self.config_manager.filtering_settings
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                persona_id = await get_persona_id(self.context, event)

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                # 提取真实用户消息
                if not req.prompt:
                    logger.warning(f"[{session_id}] req.prompt 为空，跳过记忆召回")
                    return

                actual_query = ChatroomContextParser.extract_actual_message(req.prompt)

                if actual_query != req.prompt:
                    logger.info(
                        f"[{session_id}] 检测到群聊上下文格式，已提取真实消息用于召回"
                    )

                # 执行记忆召回
                logger.info(
                    f"[{session_id}] 开始记忆召回，查询='{actual_query[:50]}...'"
                )

                recalled_memories = await self.memory_engine.search_memories(
                    query=actual_query,
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
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": mem.metadata,
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

                    # 根据配置选择注入方式
                    injection_method = self.config_manager.get(
                        "recall_engine.injection_method", "system_prompt"
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
                    else:
                        req.system_prompt = (
                            memory_str + "\n" + (req.system_prompt or "")
                        )
                        logger.info(
                            f"[{session_id}] 成功向 System Prompt 注入 {len(recalled_memories)} 条记忆"
                        )
                else:
                    logger.info(f"[{session_id}] 未找到相关记忆")

                # 存储用户消息（仅私聊）
                is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
                if not is_group and req.prompt:
                    message_to_store = ChatroomContextParser.extract_actual_message(
                        req.prompt
                    )
                    await self.conversation_manager.add_message_from_event(
                        event=event,
                        role="user",
                        content=message_to_store,
                    )

        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)

    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """在 LLM 响应后，检查是否需要进行反思和记忆存储"""
        logger.debug(
            f"[DEBUG-Reflection] 进入 handle_memory_reflection，resp.role={resp.role}"
        )

        if resp.role != "assistant":
            return

        try:
            session_id = event.unified_msg_origin
            logger.debug(f"[DEBUG-Reflection] 获取到 unified_msg_origin: {session_id}")

            if not session_id:
                logger.warning("[DEBUG-Reflection] session_id 为空，跳过反思")
                return

            # 检测异常session_id
            if "Error:" in session_id or "error:" in session_id.lower():
                logger.warning(
                    f"[{session_id}] 检测到异常的session_id，这可能导致记忆总结异常。"
                )

            # 检查响应内容是否有效（过滤空回复和错误）
            response_text = resp.completion_text
            if not response_text or not response_text.strip():
                logger.debug(f"[{session_id}] 模型返回空回复，跳过记录")
                return

            # 检查是否为错误响应
            error_indicators = [
                "api error",
                "request failed",
                "rate limit",
                "timeout",
                "connection error",
                "服务暂时不可用",
                "请求失败",
                "接口错误",
            ]
            response_lower = response_text.lower()
            if any(indicator in response_lower for indicator in error_indicators):
                logger.debug(
                    f"[{session_id}] 检测到错误响应，跳过记录: {response_text[:50]}..."
                )
                return

            # 添加助手响应
            await self.conversation_manager.add_message_from_event(
                event=event,
                role="assistant",
                content=response_text,
            )
            logger.debug(f"[DEBUG-Reflection] [{session_id}] 已添加助手响应消息")

            # 获取会话信息
            session_info = await self.conversation_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] session_info 为 None，跳过反思"
                )
                return

            # 获取实际消息数量（用于数据一致性检查）
            actual_message_count = (
                await self.conversation_manager.store.get_message_count(session_id)
            )

            # 数据一致性检查
            if session_info.message_count != actual_message_count:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] 数据不一致! "
                    f"sessions表记录={session_info.message_count}, "
                    f"实际消息数={actual_message_count}"
                )

            # 使用实际消息数量
            total_messages = actual_message_count

            # 检查是否满足总结条件
            trigger_rounds = self.config_manager.get(
                "reflection_engine.summary_trigger_rounds", 10
            )

            # 获取上次总结的位置
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )

            # 检查 last_summarized_index 是否超出实际消息数量
            if last_summarized_index > total_messages:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] last_summarized_index({last_summarized_index}) "
                    f"> 实际消息数({total_messages})，重置为0"
                )
                last_summarized_index = 0
                await self.conversation_manager.update_session_metadata(
                    session_id, "last_summarized_index", 0
                )

            # 计算未总结的消息数量
            unsummarized_messages = total_messages - last_summarized_index
            unsummarized_rounds = unsummarized_messages // 2

            # 检查是否有待处理的失败总结
            pending_summary = await self.conversation_manager.get_session_metadata(
                session_id, "pending_summary", None
            )

            logger.info(
                f"[DEBUG-Reflection] [{session_id}] 总消息数: {total_messages}, "
                f"上次总结位置: {last_summarized_index}, "
                f"未总结轮数: {unsummarized_rounds}, "
                f"触发阈值: {trigger_rounds}轮, "
                f"待处理失败总结: {pending_summary is not None}"
            )

            # 当未总结的轮数达到触发阈值时进行总结
            if unsummarized_rounds >= trigger_rounds:
                logger.info(
                    f"[{session_id}] 未总结轮数达到 {unsummarized_rounds} 轮，启动记忆反思任务"
                )

                # 计算总结范围（考虑待处理的失败总结）
                start_index = last_summarized_index
                end_index = total_messages
                retry_count = 0

                # 如果有待处理的失败总结，合并范围
                if pending_summary:
                    pending_start = pending_summary.get("start_index", start_index)
                    retry_count = pending_summary.get("retry_count", 0)

                    # 检查是否已达到最大重试次数
                    if retry_count >= 3:
                        logger.warning(
                            f"[{session_id}] 待处理总结已连续失败 {retry_count} 次，放弃该范围 "
                            f"[{pending_start}:{pending_summary.get('end_index', end_index)}]"
                        )
                        # 清除待处理记录，更新 last_summarized_index 到当前位置
                        await self.conversation_manager.update_session_metadata(
                            session_id, "pending_summary", None
                        )
                        await self.conversation_manager.update_session_metadata(
                            session_id, "last_summarized_index", end_index
                        )
                        return

                    # 合并范围：使用待处理的起始位置
                    start_index = pending_start
                    logger.info(
                        f"[{session_id}] 合并待处理失败总结，新范围 [{start_index}:{end_index}], "
                        f"重试次数: {retry_count + 1}/3"
                    )

                if end_index - start_index < 2:
                    logger.debug(f"[{session_id}] 消息数不足一轮对话，跳过总结")
                    return

                messages_to_summarize = end_index - start_index
                rounds_to_summarize = messages_to_summarize // 2

                logger.info(
                    f"[{session_id}] 滑动窗口总结: "
                    f"消息范围 [{start_index}:{end_index}]/{total_messages}, "
                    f"本次总结 {rounds_to_summarize} 轮"
                )

                # 获取需要总结的消息
                history_messages = await self.conversation_manager.get_messages_range(
                    session_id=session_id, start_index=start_index, end_index=end_index
                )

                logger.info(
                    f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结"
                )

                persona_id = await get_persona_id(self.context, event)

                # 创建后台任务进行存储
                asyncio.create_task(
                    self._storage_task(
                        session_id,
                        history_messages,
                        persona_id,
                        start_index,
                        end_index,
                        retry_count,
                    )
                )

        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)

    async def _storage_task(
        self,
        session_id: str,
        history_messages: list,
        persona_id: str | None,
        start_index: int,
        end_index: int,
        retry_count: int = 0,
    ):
        """
        后台存储任务

        Args:
            session_id: 会话ID
            history_messages: 待总结的消息列表
            persona_id: 人格ID
            start_index: 总结范围起始索引
            end_index: 总结范围结束索引
            retry_count: 当前重试次数
        """
        async with OperationContext("记忆存储", session_id):
            try:
                # 判断是否为群聊
                is_group_chat = bool(
                    history_messages[0].group_id if history_messages else False
                )
                # 备用判断：从 session_id 解析（防御性编程）
                if not is_group_chat and "GroupMessage" in session_id:
                    is_group_chat = True

                logger.info(
                    f"[{session_id}] 开始处理记忆，类型={'群聊' if is_group_chat else '私聊'}, "
                    f"范围=[{start_index}:{end_index}], 重试次数={retry_count}, "
                    f"当前人格={persona_id or '未设置'}"
                )

                # 使用 MemoryProcessor 处理对话历史
                if not self.memory_processor:
                    logger.error(f"[{session_id}] MemoryProcessor 未初始化，记录待重试")
                    await self._record_pending_summary(
                        session_id, start_index, end_index, retry_count
                    )
                    return

                try:
                    logger.info(
                        f"[{session_id}] 调用 MemoryProcessor 处理 {len(history_messages)} 条消息"
                    )
                    save_original = self.config_manager.get(
                        "reflection_engine.save_original_conversation", False
                    )

                    (
                        content,
                        metadata,
                        importance,
                    ) = await self.memory_processor.process_conversation(
                        messages=history_messages,
                        is_group_chat=is_group_chat,
                        save_original=save_original,
                        persona_id=persona_id,
                    )
                    logger.info(
                        f"[{session_id}] 已使用LLM生成结构化记忆, "
                        f"主题={metadata.get('topics', [])}, "
                        f"重要性={importance:.2f}"
                    )

                except Exception as e:
                    # LLM处理失败，记录待重试信息
                    logger.error(
                        f"[{session_id}] LLM处理失败 (重试 {retry_count + 1}/3): {e}",
                        exc_info=True,
                    )
                    await self._record_pending_summary(
                        session_id, start_index, end_index, retry_count
                    )
                    return

                # 正常流程：添加到记忆引擎
                if self.memory_engine:
                    await self.memory_engine.add_memory(
                        content=content,
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=importance,
                        metadata=metadata,
                    )

                    logger.info(
                        f"[{session_id}] 成功存储对话记忆（{len(history_messages)}条消息，重要性={importance:.2f}）"
                    )

                # 成功：更新已总结的位置，清除待处理记录
                if self.conversation_manager:
                    await self.conversation_manager.update_session_metadata(
                        session_id, "last_summarized_index", end_index
                    )
                    await self.conversation_manager.update_session_metadata(
                        session_id, "pending_summary", None
                    )
                    logger.info(
                        f"[{session_id}] 更新滑动窗口位置: last_summarized_index = {end_index}"
                    )

            except Exception as e:
                logger.error(f"[{session_id}] 存储记忆失败: {e}", exc_info=True)
                await self._record_pending_summary(
                    session_id, start_index, end_index, retry_count
                )

    async def _record_pending_summary(
        self,
        session_id: str,
        start_index: int,
        end_index: int,
        current_retry_count: int,
    ):
        """
        记录待处理的失败总结信息

        Args:
            session_id: 会话ID
            start_index: 总结范围起始索引
            end_index: 总结范围结束索引
            current_retry_count: 当前重试次数
        """
        if not self.conversation_manager:
            return

        new_retry_count = current_retry_count + 1
        pending_summary = {
            "start_index": start_index,
            "end_index": end_index,
            "retry_count": new_retry_count,
        }

        await self.conversation_manager.update_session_metadata(
            session_id, "pending_summary", pending_summary
        )

        logger.warning(
            f"[{session_id}] 记录待重试总结: 范围=[{start_index}:{end_index}], "
            f"重试次数={new_retry_count}/3"
        )

    def _remove_injected_memories_from_context(
        self, req: ProviderRequest, session_id: str
    ) -> int:
        """从对话历史、system_prompt和prompt中删除之前注入的记忆片段"""
        import re

        from .base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

        removed_count = 0

        # 编译清理正则
        pattern = (
            re.escape(MEMORY_INJECTION_HEADER)
            + r".*?"
            + re.escape(MEMORY_INJECTION_FOOTER)
        )

        try:
            # 清理 system_prompt
            if hasattr(req, "system_prompt") and req.system_prompt:
                if isinstance(req.system_prompt, str):
                    original_prompt = req.system_prompt
                    if (
                        MEMORY_INJECTION_HEADER in original_prompt
                        and MEMORY_INJECTION_FOOTER in original_prompt
                    ):
                        cleaned_prompt = re.sub(
                            pattern, "", original_prompt, flags=re.DOTALL
                        )
                        cleaned_prompt = re.sub(
                            r"\n{3,}", "\n\n", cleaned_prompt
                        ).strip()
                        req.system_prompt = cleaned_prompt

                        if cleaned_prompt != original_prompt:
                            removed_count += 1

            # 清理 prompt（处理 user_message_before/after 注入方式）
            if hasattr(req, "prompt") and req.prompt:
                if isinstance(req.prompt, str):
                    original_prompt = req.prompt
                    if (
                        MEMORY_INJECTION_HEADER in original_prompt
                        and MEMORY_INJECTION_FOOTER in original_prompt
                    ):
                        cleaned_prompt = re.sub(
                            pattern, "", original_prompt, flags=re.DOTALL
                        )
                        cleaned_prompt = re.sub(
                            r"\n{3,}", "\n\n", cleaned_prompt
                        ).strip()
                        req.prompt = cleaned_prompt

                        if cleaned_prompt != original_prompt:
                            removed_count += 1
                            logger.debug(
                                f"[{session_id}] 已从 req.prompt 中清理旧记忆片段"
                            )

            # 清理对话历史
            if hasattr(req, "contexts") and req.contexts:
                # original_length = len(req.contexts)
                filtered_contexts = []

                for msg in req.contexts:
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                    if isinstance(content, str):
                        if (
                            MEMORY_INJECTION_HEADER in content
                            and MEMORY_INJECTION_FOOTER in content
                        ):
                            removed_count += 1
                            continue

                    filtered_contexts.append(msg)

                req.contexts = filtered_contexts

            if removed_count > 0:
                logger.info(
                    f"[{session_id}] 成功清理旧记忆片段，共删除 {removed_count} 处注入内容"
                )

        except Exception as e:
            logger.error(f"[{session_id}] 删除注入记忆时发生错误: {e}", exc_info=True)

        return removed_count

    def _is_duplicate_message(self, message_id: str) -> bool:
        """检查消息是否已经处理过"""
        current_time = time.time()

        # 清理过期缓存
        expired_keys = [
            key
            for key, timestamp in self._message_dedup_cache.items()
            if current_time - timestamp > self._dedup_cache_ttl
        ]
        for key in expired_keys:
            del self._message_dedup_cache[key]

        return message_id in self._message_dedup_cache

    def _mark_message_processed(self, message_id: str):
        """标记消息已处理"""
        current_time = time.time()

        if len(self._message_dedup_cache) >= self._dedup_cache_max_size:
            oldest_key = min(self._message_dedup_cache.items(), key=lambda x: x[1])[0]
            del self._message_dedup_cache[oldest_key]

        self._message_dedup_cache[message_id] = current_time

    def _extract_message_content(self, event: AstrMessageEvent) -> str:
        """提取消息内容,包括非文本消息的类型描述"""
        from astrbot.core.message.components import (
            At,
            AtAll,
            Face,
            File,
            Forward,
            Image,
            Plain,
            Record,
            Reply,
            Video,
        )

        parts = []
        base_text = event.get_message_str()

        if base_text:
            parts.append(base_text)

        for component in event.get_messages():
            if isinstance(component, Image):
                parts.append("[图片]")
            elif isinstance(component, Record):
                parts.append("[语音]")
            elif isinstance(component, Video):
                parts.append("[视频]")
            elif isinstance(component, File):
                file_name = component.name or "未知文件"
                parts.append(f"[文件: {file_name}]")
            elif isinstance(component, Face):
                parts.append(f"[表情:{component.id}]")
            elif isinstance(component, At):
                if isinstance(component, AtAll):
                    parts.append("[At:全体成员]")
                else:
                    parts.append(f"[At:{component.qq}]")
            elif isinstance(component, Forward):
                parts.append("[转发消息]")
            elif isinstance(component, Reply):
                if component.message_str:
                    parts.append(f"[引用: {component.message_str[:30]}]")
                else:
                    parts.append("[引用消息]")
            elif not isinstance(component, Plain):
                parts.append(f"[{component.type}]")

        return " ".join(parts).strip()

    async def _update_message_metadata(self, message):
        """更新消息的metadata到数据库"""
        if not self.conversation_manager or not self.conversation_manager.store:
            return

        await self.conversation_manager.store.update_message_metadata(
            message.id, message.metadata
        )

    async def _enforce_message_limit(self, session_id: str):
        """执行消息数量上限控制，只删除已被总结的消息"""
        if not self.conversation_manager:
            return

        max_messages = self.config_manager.get(
            "session_manager.max_messages_per_session", 1000
        )

        if (
            not self.conversation_manager.store
            or not self.conversation_manager.store.connection
        ):
            return

        try:
            conn = self.conversation_manager.store.connection

            # 获取实际消息数量
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )

            if actual_count <= max_messages:
                return

            # 获取已总结的消息位置
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )

            # 计算需要删除的数量
            to_delete = actual_count - max_messages

            # 只能删除已总结的消息，不能删除未总结的
            safe_to_delete = min(to_delete, last_summarized_index)

            if safe_to_delete <= 0:
                logger.debug(
                    f"[{session_id}] 无可删除消息: "
                    f"需删除={to_delete}, 已总结={last_summarized_index}"
                )
                return

            logger.info(
                f"[{session_id}] 开始清理已总结消息: "
                f"总数={actual_count}, 上限={max_messages}, "
                f"需删除={to_delete}, 已总结={last_summarized_index}, "
                f"实际删除={safe_to_delete}"
            )

            # 删除最旧的已总结消息
            cursor = await conn.execute(
                """
                DELETE FROM messages
                WHERE id IN (
                    SELECT id FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
                """,
                (session_id, safe_to_delete),
            )

            actually_deleted = cursor.rowcount

            # 更新 last_summarized_index（减去已删除的数量）
            new_summarized_index = last_summarized_index - actually_deleted
            await self.conversation_manager.update_session_metadata(
                session_id, "last_summarized_index", max(0, new_summarized_index)
            )

            # 更新 sessions 表的 message_count
            new_actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )

            await conn.execute(
                """
                UPDATE sessions
                SET message_count = ?
                WHERE session_id = ?
                """,
                (new_actual_count, session_id),
            )

            await conn.commit()

            # 清除缓存
            if session_id in self.conversation_manager._cache:
                del self.conversation_manager._cache[session_id]

            logger.info(
                f"[{session_id}] 消息清理完成: "
                f"删除={actually_deleted}条, 剩余={new_actual_count}条, "
                f"总结索引: {last_summarized_index} -> {new_summarized_index}"
            )

        except Exception as e:
            logger.error(f"[{session_id}] 删除旧消息失败: {e}", exc_info=True)
