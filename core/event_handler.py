"""
事件处理器
负责处理AstrBot事件钩子
"""

import asyncio
import re
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

        # 后台存储任务跟踪
        self._storage_tasks: set[asyncio.Task] = set()
        self._sessions_summarizing: set[str] = set()
        self._sessions_summary_rerun_requested: set[str] = set()
        self._shutting_down = False

    async def _build_conversation_session_id(self, event: AstrMessageEvent) -> str:
        """
        构建插件内部会话键：unified_msg_origin + conversation_id。
        这样在 AstrBot /new 切换对话时，插件可自动隔离待总结窗口。
        """
        base_session_id = event.unified_msg_origin
        try:
            conversation_manager = getattr(self.context, "conversation_manager", None)
            if not conversation_manager:
                return base_session_id

            conversation_id = await conversation_manager.get_curr_conversation_id(
                base_session_id
            )
            if not conversation_id:
                return base_session_id

            return f"{base_session_id}::conv::{conversation_id}"
        except Exception as e:
            logger.debug(f"构建对话级会话键失败，回退为 unified_msg_origin: {e}")
            return base_session_id

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
            conversation_session_id = await self._build_conversation_session_id(event)

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
                session_id=conversation_session_id,
            )

            # 标记是否为Bot消息
            message.metadata["is_bot_message"] = is_bot_message
            await self._update_message_metadata(message)

            # 执行消息数量上限控制
            await self._enforce_message_limit(conversation_session_id)

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
            conversation_session_id = await self._build_conversation_session_id(event)
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
                    removed = self._remove_injected_memories_from_context(
                        req, session_id
                    )
                    if removed > 0:
                        logger.info(
                            f"[{session_id}] 已清理 {removed} 处历史记忆注入片段"
                        )

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

                # 诊断：记录本次请求输入结构（仅日志，不改变业务流程）
                self._log_recall_request_shape(session_id, event, req)

                # 严格使用“当前 req”作为本轮输入来源：
                # - 文本来自 req.prompt / extra_user_content_parts
                # - 图片转述来自 extra_user_content_parts 中的 <image_caption>
                # 不读取历史、不过滤回退，避免跨轮错位。
                actual_query, image_caption, is_image_turn = (
                    self._resolve_current_turn_input_from_request(event, req)
                )

                capture_image_caption = self.config_manager.get(
                    "session_manager.capture_image_caption", True
                )
                logger.info(
                    f"[{session_id}] [RecallQuery] source=current_req, "
                    f"is_image_turn={is_image_turn}, "
                    f"text_len={len((actual_query or '').strip())}, "
                    f"caption_len={len((image_caption or '').strip())}"
                )

                if getattr(req, "prompt", None) and actual_query != req.prompt:
                    logger.info(
                        f"[{session_id}] 检测到群聊上下文格式，已提取真实消息用于召回"
                    )

                # 执行记忆召回
                # 图片轮次：使用“文本 + 图片转述”拼接查询；无图片转述时回退到文本查询
                if is_image_turn:
                    recall_query = self._build_image_turn_recall_query(
                        actual_query, image_caption
                    )
                    logger.debug(
                        f"[{session_id}] [RecallQuery] mode=image, "
                        f"text_len={len((actual_query or '').strip())}, "
                        f"caption_len={len((image_caption or '').strip())}, "
                        f"query_len={len(recall_query)}, "
                        f"query_preview='{recall_query[:120].replace(chr(10), ' ')}'"
                    )
                else:
                    recall_query = actual_query.strip()
                    logger.debug(
                        f"[{session_id}] [RecallQuery] mode=text, "
                        f"text_len={len((actual_query or '').strip())}, "
                        f"query_len={len(recall_query)}, "
                        f"query_preview='{recall_query[:120].replace(chr(10), ' ')}'"
                    )

                if not recall_query:
                    logger.warning(
                        f"[{session_id}] 未提取到可用召回查询，跳过记忆召回（仅保留后续写库流程）"
                    )
                    recalled_memories = []
                else:
                    logger.info(
                        f"[{session_id}] 开始记忆召回，查询='{recall_query[:50]}...'"
                    )

                    recalled_memories = await self.memory_engine.search_memories(
                        query=recall_query,
                        k=self.config_manager.get("recall_engine.top_k", 5),
                        session_id=recall_session_id,
                        persona_id=recall_persona_id,
                    )

                if recalled_memories:
                    logger.info(
                        f"[{session_id}] 检索到 {len(recalled_memories)} 条记忆"
                    )

                    # 格式化并注入记忆
                    include_memory_time = self.config_manager.get(
                        "recall_engine.include_memory_time", False
                    )
                    memory_list = [
                        {
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": mem.metadata,
                            "timestamp": mem.metadata.get("create_time")
                            if include_memory_time
                            else None,
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
                if not is_group:
                    if is_image_turn:
                        text_for_store = actual_query.strip()
                        caption_text = (image_caption or "").strip()

                        if caption_text:
                            if text_for_store:
                                text_for_store = (
                                    f"{text_for_store}\n\n<image_caption>{caption_text}</image_caption>"
                                )
                            else:
                                text_for_store = (
                                    f"[图片]\n\n<image_caption>{caption_text}</image_caption>"
                                )
                        else:
                            if not text_for_store:
                                text_for_store = "[图片]"
                            logger.warning(
                                f"[{session_id}] 私聊图片消息未拿到图片描述，已回退仅写入文本/占位符"
                            )

                        message_to_store = self._prepare_user_message_for_storage(
                            text_for_store,
                            enable_image_caption=capture_image_caption,
                        )
                        await self.conversation_manager.add_message_from_event(
                            event=event,
                            role="user",
                            content=message_to_store,
                            session_id=conversation_session_id,
                        )
                    elif actual_query.strip():
                        message_to_store = self._prepare_user_message_for_storage(
                            actual_query,
                            enable_image_caption=capture_image_caption,
                        )
                        await self.conversation_manager.add_message_from_event(
                            event=event,
                            role="user",
                            content=message_to_store,
                            session_id=conversation_session_id,
                        )
                    else:
                        logger.warning(
                            f"[{session_id}] 私聊未提取到本轮用户输入与图片描述，跳过写库（严格模式）"
                        )
                # 群聊消息已在全量捕获时落库为占位符，这里补写图片描述，避免总结丢失图像语义
                elif is_group and image_caption:
                    await self._patch_recent_group_image_message(
                        conversation_session_id, image_caption
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
            conversation_session_id = await self._build_conversation_session_id(event)
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
                session_id=conversation_session_id,
            )
            logger.debug(
                f"[DEBUG-Reflection] [{conversation_session_id}] 已添加助手响应消息"
            )

            # 获取会话信息
            session_info = await self.conversation_manager.get_session_info(
                conversation_session_id
            )
            if not session_info:
                logger.warning(
                    f"[DEBUG-Reflection] [{conversation_session_id}] session_info 为 None，跳过反思"
                )
                return

            # 获取实际消息数量（用于数据一致性检查）
            actual_message_count = (
                await self.conversation_manager.store.get_message_count(
                    conversation_session_id
                )
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
                    conversation_session_id, "last_summarized_index", 0
                )
            )

            # 检查 last_summarized_index 是否超出实际消息数量
            # 这种情况通常发生在消息被删除后
            if last_summarized_index > total_messages:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] last_summarized_index({last_summarized_index}) "
                    f"> 实际消息数({total_messages})，调整为当前消息总数"
                )
                # 调整为当前消息总数，而非归零（避免重复处理已总结的内容）
                last_summarized_index = total_messages
                await self.conversation_manager.update_session_metadata(
                    conversation_session_id, "last_summarized_index", total_messages
                )

            # 计算未总结的消息数量
            unsummarized_messages = total_messages - last_summarized_index
            unsummarized_rounds = unsummarized_messages // 2

            # 检查是否有待处理的失败总结
            pending_summary = await self.conversation_manager.get_session_metadata(
                conversation_session_id, "pending_summary", None
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
                            conversation_session_id, "pending_summary", None
                        )
                        await self.conversation_manager.update_session_metadata(
                            conversation_session_id, "last_summarized_index", end_index
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
                    session_id=conversation_session_id,
                    start_index=start_index,
                    end_index=end_index,
                )

                logger.info(
                    f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结"
                )

                persona_id = await get_persona_id(self.context, event)
                await self.conversation_manager.update_session_metadata(
                    conversation_session_id, "last_persona_id", persona_id
                )

                # 创建后台任务进行存储（跟踪任务）
                self._create_storage_task(
                    memory_session_id=session_id,
                    conversation_session_id=conversation_session_id,
                    history_messages=history_messages,
                    persona_id=persona_id,
                    start_index=start_index,
                    end_index=end_index,
                    retry_count=retry_count,
                )

        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)

    async def _storage_task(
        self,
        memory_session_id: str,
        conversation_session_id: str,
        history_messages: list,
        persona_id: str | None,
        start_index: int,
        end_index: int,
        retry_count: int = 0,
    ):
        """
        后台存储任务

        Args:
            memory_session_id: 写入长期记忆时使用的会话ID（unified_msg_origin）
            conversation_session_id: 插件对话窗口会话ID（包含conversation_id）
            history_messages: 待总结的消息列表
            persona_id: 人格ID
            start_index: 总结范围起始索引
            end_index: 总结范围结束索引
            retry_count: 当前重试次数
        """
        async with OperationContext("记忆存储", conversation_session_id):
            try:
                # 判断是否为群聊
                is_group_chat = bool(
                    history_messages[0].group_id if history_messages else False
                )
                # 备用判断：从 session_id 解析（防御性编程）
                if not is_group_chat and "GroupMessage" in conversation_session_id:
                    is_group_chat = True

                logger.info(
                    f"[{conversation_session_id}] 开始处理记忆，类型={'群聊' if is_group_chat else '私聊'}, "
                    f"范围=[{start_index}:{end_index}], 重试次数={retry_count}, "
                    f"当前人格={persona_id or '未设置'}"
                )

                # 使用 MemoryProcessor 处理对话历史
                if not self.memory_processor:
                    logger.error(
                        f"[{conversation_session_id}] MemoryProcessor 未初始化，记录待重试"
                    )
                    await self._record_pending_summary(
                        conversation_session_id, start_index, end_index, retry_count
                    )
                    return

                try:
                    logger.info(
                        f"[{conversation_session_id}] 调用 MemoryProcessor 处理 {len(history_messages)} 条消息"
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
                        f"[{conversation_session_id}] 已使用LLM生成结构化记忆, "
                        f"主题={metadata.get('topics', [])}, "
                        f"重要性={importance:.2f}"
                    )

                except Exception as e:
                    # LLM处理失败，记录待重试信息
                    logger.error(
                        f"[{conversation_session_id}] LLM处理失败 (重试 {retry_count + 1}/3): {e}",
                        exc_info=True,
                    )
                    await self._record_pending_summary(
                        conversation_session_id, start_index, end_index, retry_count
                    )
                    return

                # 正常流程：添加到记忆引擎
                if self.memory_engine:
                    await self.memory_engine.add_memory(
                        content=content,
                        session_id=memory_session_id,
                        persona_id=persona_id,
                        importance=importance,
                        metadata=metadata,
                    )

                    logger.info(
                        f"[{conversation_session_id}] 成功存储对话记忆（{len(history_messages)}条消息，重要性={importance:.2f}）"
                    )

                # 成功：更新已总结的位置，清除待处理记录
                if self.conversation_manager:
                    await self.conversation_manager.update_session_metadata(
                        conversation_session_id, "last_summarized_index", end_index
                    )
                    await self.conversation_manager.update_session_metadata(
                        conversation_session_id, "pending_summary", None
                    )
                    logger.info(
                        f"[{conversation_session_id}] 更新滑动窗口位置: last_summarized_index = {end_index}"
                    )

            except Exception as e:
                logger.error(
                    f"[{conversation_session_id}] 存储记忆失败: {e}", exc_info=True
                )
                await self._record_pending_summary(
                    conversation_session_id, start_index, end_index, retry_count
                )

    def _create_storage_task(
        self,
        memory_session_id: str,
        conversation_session_id: str,
        history_messages: list,
        persona_id: str | None,
        start_index: int,
        end_index: int,
        retry_count: int = 0,
    ) -> None:
        """创建并跟踪存储任务，避免同一会话并发重复总结。"""
        if self._shutting_down:
            return
        if conversation_session_id in self._sessions_summarizing:
            self._sessions_summary_rerun_requested.add(conversation_session_id)
            logger.debug(
                f"[{conversation_session_id}] 已有总结任务在执行，标记本会话需在完成后重跑"
            )
            return

        self._sessions_summarizing.add(conversation_session_id)

        initial_task_args = {
            "memory_session_id": memory_session_id,
            "conversation_session_id": conversation_session_id,
            "history_messages": history_messages,
            "persona_id": persona_id,
            "start_index": start_index,
            "end_index": end_index,
            "retry_count": retry_count,
        }

        async def _runner():
            try:
                task_args = initial_task_args

                while not self._shutting_down and task_args:
                    await self._storage_task(**task_args)
                    task_args = None

                    if conversation_session_id in self._sessions_summary_rerun_requested:
                        self._sessions_summary_rerun_requested.discard(
                            conversation_session_id
                        )
                        task_args = await self._build_follow_up_summary_task_args(
                            conversation_session_id
                        )
            finally:
                self._sessions_summarizing.discard(conversation_session_id)
                if (
                    not self._shutting_down
                    and conversation_session_id
                    in self._sessions_summary_rerun_requested
                ):
                    self._sessions_summary_rerun_requested.discard(
                        conversation_session_id
                    )
                    follow_up_args = await self._build_follow_up_summary_task_args(
                        conversation_session_id
                    )
                    if follow_up_args:
                        self._create_storage_task(**follow_up_args)

        task = asyncio.create_task(_runner())
        self._storage_tasks.add(task)
        task.add_done_callback(self._storage_tasks.discard)

    async def _build_follow_up_summary_task_args(
        self, conversation_session_id: str
    ) -> dict[str, Any] | None:
        """为同会话后续重跑构建最新总结任务参数。"""
        if self._shutting_down or not self.conversation_manager:
            return None

        total_messages = await self.conversation_manager.store.get_message_count(
            conversation_session_id
        )
        last_summarized_index = await self.conversation_manager.get_session_metadata(
            conversation_session_id, "last_summarized_index", 0
        )
        if last_summarized_index > total_messages:
            last_summarized_index = total_messages
            await self.conversation_manager.update_session_metadata(
                conversation_session_id, "last_summarized_index", total_messages
            )

        pending_summary = await self.conversation_manager.get_session_metadata(
            conversation_session_id, "pending_summary", None
        )

        start_index = last_summarized_index
        end_index = total_messages
        retry_count = 0

        if pending_summary:
            pending_start = pending_summary.get("start_index", start_index)
            retry_count = pending_summary.get("retry_count", 0)

            if retry_count >= 3:
                await self.conversation_manager.update_session_metadata(
                    conversation_session_id, "pending_summary", None
                )
                await self.conversation_manager.update_session_metadata(
                    conversation_session_id, "last_summarized_index", end_index
                )
                return None

            start_index = pending_start
        else:
            trigger_rounds = self.config_manager.get(
                "reflection_engine.summary_trigger_rounds", 10
            )
            unsummarized_messages = max(0, total_messages - last_summarized_index)
            unsummarized_rounds = unsummarized_messages // 2
            if unsummarized_rounds < trigger_rounds:
                return None

        if end_index - start_index < 2:
            return None

        history_messages = await self.conversation_manager.get_messages_range(
            session_id=conversation_session_id,
            start_index=start_index,
            end_index=end_index,
        )
        if len(history_messages) < 2:
            return None

        persona_id = await self.conversation_manager.get_session_metadata(
            conversation_session_id, "last_persona_id", None
        )
        memory_session_id = conversation_session_id.split("::conv::", 1)[0]

        return {
            "memory_session_id": memory_session_id,
            "conversation_session_id": conversation_session_id,
            "history_messages": history_messages,
            "persona_id": persona_id,
            "start_index": start_index,
            "end_index": end_index,
            "retry_count": retry_count,
        }

    async def run_idle_summary_check(self) -> int:
        """
        扫描空闲会话并触发自动总结。
        返回本轮触发的总结任务数量。
        """
        if self._shutting_down or not self.conversation_manager:
            return 0
        if not self.config_manager.get("reflection_engine.enable_idle_auto_summary", False):
            return 0

        timeout_seconds = int(
            self.config_manager.get(
                "reflection_engine.idle_summary_timeout_seconds", 1800
            )
        )
        now = time.time()
        triggered = 0

        try:
            sessions = await self.conversation_manager.get_recent_sessions(limit=None)
            for session in sessions:
                conversation_session_id = session.session_id
                if not conversation_session_id:
                    continue
                if conversation_session_id in self._sessions_summarizing:
                    continue

                idle_seconds = now - float(session.last_active_at or 0)
                if idle_seconds < timeout_seconds:
                    continue

                total_messages = await self.conversation_manager.store.get_message_count(
                    conversation_session_id
                )
                last_summarized_index = await self.conversation_manager.get_session_metadata(
                    conversation_session_id, "last_summarized_index", 0
                )
                if last_summarized_index > total_messages:
                    last_summarized_index = total_messages
                    await self.conversation_manager.update_session_metadata(
                        conversation_session_id, "last_summarized_index", total_messages
                    )

                # 空闲自动总结至少要求一轮对话
                unsummarized_messages = max(0, total_messages - last_summarized_index)
                if unsummarized_messages < 2:
                    continue

                pending_summary = await self.conversation_manager.get_session_metadata(
                    conversation_session_id, "pending_summary", None
                )

                start_index = last_summarized_index
                end_index = total_messages
                retry_count = 0

                if pending_summary:
                    pending_start = pending_summary.get("start_index", start_index)
                    retry_count = pending_summary.get("retry_count", 0)

                    if retry_count >= 3:
                        await self.conversation_manager.update_session_metadata(
                            conversation_session_id, "pending_summary", None
                        )
                        await self.conversation_manager.update_session_metadata(
                            conversation_session_id, "last_summarized_index", end_index
                        )
                        continue

                    start_index = pending_start

                if end_index - start_index < 2:
                    continue

                history_messages = await self.conversation_manager.get_messages_range(
                    session_id=conversation_session_id,
                    start_index=start_index,
                    end_index=end_index,
                )
                if len(history_messages) < 2:
                    continue

                persona_id = await self.conversation_manager.get_session_metadata(
                    conversation_session_id, "last_persona_id", None
                )
                memory_session_id = conversation_session_id.split("::conv::", 1)[0]

                self._create_storage_task(
                    memory_session_id=memory_session_id,
                    conversation_session_id=conversation_session_id,
                    history_messages=history_messages,
                    persona_id=persona_id,
                    start_index=start_index,
                    end_index=end_index,
                    retry_count=retry_count,
                )
                triggered += 1

            if triggered > 0:
                logger.info(f"[idle-summary] 本轮触发 {triggered} 个空闲会话自动总结任务")
        except Exception as e:
            logger.error(f"[idle-summary] 扫描空闲会话失败: {e}", exc_info=True)

        return triggered

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

        # 编译清理正则(使用DOTALL确保.匹配换行符)
        pattern = re.compile(
            re.escape(MEMORY_INJECTION_HEADER)
            + r".*?"
            + re.escape(MEMORY_INJECTION_FOOTER),
            flags=re.DOTALL,
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
                        cleaned_prompt = pattern.sub("", original_prompt)
                        cleaned_prompt = re.sub(
                            r"\n{3,}", "\n\n", cleaned_prompt
                        ).strip()
                        req.system_prompt = cleaned_prompt

                        if cleaned_prompt != original_prompt:
                            removed_count += 1
                            logger.debug(
                                f"[{session_id}] 从system_prompt中清理记忆片段 "
                                f"(原长度={len(original_prompt)}, 新长度={len(cleaned_prompt)})"
                            )

            # 清理 prompt（处理 user_message_before/after 注入方式）
            if hasattr(req, "prompt") and req.prompt:
                if isinstance(req.prompt, str):
                    original_prompt = req.prompt
                    if (
                        MEMORY_INJECTION_HEADER in original_prompt
                        and MEMORY_INJECTION_FOOTER in original_prompt
                    ):
                        cleaned_prompt = pattern.sub("", original_prompt)
                        cleaned_prompt = re.sub(
                            r"\n{3,}", "\n\n", cleaned_prompt
                        ).strip()
                        req.prompt = cleaned_prompt

                        if cleaned_prompt != original_prompt:
                            removed_count += 1
                            logger.debug(
                                f"[{session_id}] 从req.prompt中清理记忆片段 "
                                f"(原长度={len(original_prompt)}, 新长度={len(cleaned_prompt)})"
                            )

            # 清理对话历史
            if hasattr(req, "contexts") and req.contexts:
                filtered_contexts = []

                for idx, msg in enumerate(req.contexts):
                    # 处理三种格式:
                    # 1. 字符串格式: "user: xxx"
                    # 2. 字典+字符串内容: {"role": "user", "content": "xxx"}
                    # 3. 字典+列表内容 (多模态): {"role": "user", "content": [{"type": "text", "text": "xxx"}]}

                    if isinstance(msg, str):
                        # 格式1: 字符串
                        content = msg
                    elif isinstance(msg, dict):
                        content = msg.get("content", "")

                        # 格式2和3: 字典
                        if not isinstance(content, (str, list)):
                            # 未知content类型,保留原消息
                            filtered_contexts.append(msg)
                            continue
                    else:
                        # 未知msg类型,保留原消息
                        filtered_contexts.append(msg)
                        continue

                    # 处理字符串内容
                    if isinstance(content, str):
                        has_header = MEMORY_INJECTION_HEADER in content
                        has_footer = MEMORY_INJECTION_FOOTER in content

                        if has_header and has_footer:
                            cleaned_content = pattern.sub("", content).strip()
                            cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)

                            if not cleaned_content:
                                removed_count += 1
                                continue

                            if cleaned_content != content:
                                removed_count += 1
                                if isinstance(msg, str):
                                    filtered_contexts.append(cleaned_content)
                                else:
                                    msg_copy = msg.copy()
                                    msg_copy["content"] = cleaned_content
                                    filtered_contexts.append(msg_copy)
                                continue

                    # 处理列表内容 (多模态格式)
                    elif isinstance(content, list):
                        cleaned_parts = []
                        has_changes = False

                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part.get("text", "")
                                if isinstance(text, str):
                                    has_header = MEMORY_INJECTION_HEADER in text
                                    has_footer = MEMORY_INJECTION_FOOTER in text

                                    if has_header and has_footer:
                                        cleaned_text = pattern.sub("", text).strip()
                                        cleaned_text = re.sub(
                                            r"\n{3,}", "\n\n", cleaned_text
                                        )

                                        # 如果清理后为空,跳过这个part
                                        if not cleaned_text:
                                            has_changes = True
                                            continue

                                        # 如果清理后有内容,保留清理后的part
                                        if cleaned_text != text:
                                            has_changes = True
                                            removed_count += 1
                                            part_copy = part.copy()
                                            part_copy["text"] = cleaned_text
                                            cleaned_parts.append(part_copy)
                                            continue

                            cleaned_parts.append(part)

                        # 如果整个content清理后为空,跳过整条消息
                        if not cleaned_parts:
                            removed_count += 1
                            continue

                        # 如果有修改,保存清理后的消息
                        if has_changes:
                            msg_copy = msg.copy()
                            msg_copy["content"] = cleaned_parts
                            filtered_contexts.append(msg_copy)
                            continue

                    # 未匹配到记忆标记,保留原消息
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

        # 批量清理：先清理过期项
        expired_keys = [
            key
            for key, timestamp in self._message_dedup_cache.items()
            if current_time - timestamp > self._dedup_cache_ttl
        ]
        for key in expired_keys:
            del self._message_dedup_cache[key]

        # 如果仍然超过上限的80%，批量删除最旧的50%
        if len(self._message_dedup_cache) > self._dedup_cache_max_size * 0.8:
            sorted_items = sorted(self._message_dedup_cache.items(), key=lambda x: x[1])
            to_remove = len(sorted_items) // 2
            for key, _ in sorted_items[:to_remove]:
                del self._message_dedup_cache[key]

        return message_id in self._message_dedup_cache

    def _mark_message_processed(self, message_id: str):
        """标记消息已处理"""
        self._message_dedup_cache[message_id] = time.time()

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

    @staticmethod
    def _extract_image_caption(content: str) -> str | None:
        """从内容中提取图片描述标签文本（兼容 <image_caption>/<picture>）。"""
        if not content:
            return None

        matches: list[str] = []
        matches.extend(
            re.findall(r"<image_caption>(.*?)</image_caption>", content, re.DOTALL)
        )
        matches.extend(re.findall(r"<picture>(.*?)</picture>", content, re.DOTALL))
        if not matches:
            return None

        # 多图场景按出现顺序拼接，避免丢失信息
        normalized_parts = []
        for part in matches:
            text = re.sub(r"\s+", " ", (part or "").strip())
            if text and text not in normalized_parts:
                normalized_parts.append(text)

        if not normalized_parts:
            return None

        merged = "；".join(normalized_parts)
        # 控制长度，避免会话消息无限膨胀
        return merged[:1200]

    @staticmethod
    def _extract_text_from_extra_part(part: Any) -> str:
        """从 req.extra_user_content_parts 的单个 part 提取文本。"""
        if isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                return part.get("text", "")
            return ""

        ptype = getattr(part, "type", None)
        ptext = getattr(part, "text", None)
        if ptype == "text" and isinstance(ptext, str):
            return ptext

        # 兼容 Pydantic 模型对象
        model_dump = getattr(part, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
                if (
                    isinstance(dumped, dict)
                    and dumped.get("type") == "text"
                    and isinstance(dumped.get("text"), str)
                ):
                    return dumped.get("text", "")
            except Exception:
                return ""
        return ""

    def _collect_extra_user_text_parts(self, req: ProviderRequest) -> list[str]:
        """收集 req.extra_user_content_parts 中的文本片段。"""
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            return []

        texts: list[str] = []
        for part in parts:
            text = self._extract_text_from_extra_part(part).strip()
            if text:
                texts.append(text)
        return texts

    @staticmethod
    def _strip_non_user_payload_text(content: str) -> str:
        """移除附加块中的系统/附件标记，仅保留用户自然语言文本。"""
        text = content or ""
        text = re.sub(r"<image_caption>.*?</image_caption>", "", text, flags=re.DOTALL)
        text = re.sub(r"<picture>.*?</picture>", "", text, flags=re.DOTALL)
        text = re.sub(
            r"<system_reminder>.*?</system_reminder>", "", text, flags=re.DOTALL
        )
        text = re.sub(r"\[Image Attachment:[^\]]+\]", "", text)
        text = re.sub(r"\[File Attachment:[^\]]+\]", "", text)
        text = re.sub(r"\[图片\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _resolve_current_turn_input_from_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> tuple[str, str | None, bool]:
        """
        严格模式：只从当前 req 提取本轮输入，不读历史，不做回退。
        返回 (user_text, image_caption, is_image_turn)。
        """
        prompt_raw = getattr(req, "prompt", None)
        prompt_text = ""
        if isinstance(prompt_raw, str) and prompt_raw.strip():
            prompt_text = ChatroomContextParser.extract_actual_message(prompt_raw).strip()

        extra_text_parts = self._collect_extra_user_text_parts(req)
        extra_joined = "\n".join(extra_text_parts)

        # 图片转述优先从 extra parts 提取（AstrBot 主链路会把当前轮 caption 放这里）
        image_caption = self._extract_image_caption(extra_joined)
        if not image_caption:
            image_caption = self._extract_image_caption(prompt_text)

        text_parts: list[str] = []
        if prompt_text:
            text_parts.append(prompt_text)
        for part_text in extra_text_parts:
            cleaned = self._strip_non_user_payload_text(part_text)
            if cleaned:
                text_parts.append(cleaned)

        # 去重并保持顺序
        dedup_text_parts: list[str] = []
        for text in text_parts:
            if text not in dedup_text_parts:
                dedup_text_parts.append(text)
        user_text = "\n".join(dedup_text_parts).strip()

        has_attachment_hint = any("[Image Attachment:" in t for t in extra_text_parts)
        has_unresolved_image = bool(getattr(req, "image_urls", None))
        is_image_turn = bool(
            self._is_image_event(event)
            or has_attachment_hint
            or has_unresolved_image
            or image_caption
        )
        return user_text, image_caption, is_image_turn

    def _extract_event_image_hints(self, event: AstrMessageEvent) -> list[str]:
        """从事件图片组件提取可用于匹配 contexts 的标识（如临时文件名/路径片段）。"""
        hints: list[str] = []
        if not hasattr(event, "get_messages"):
            return hints

        try:
            for comp in event.get_messages() or []:
                ctype = str(getattr(comp, "type", "")).lower()
                cname = comp.__class__.__name__.lower()
                if "image" not in ctype and "image" not in cname:
                    continue

                for attr in ("path", "file", "url", "src", "name"):
                    val = getattr(comp, attr, None)
                    if isinstance(val, str) and val.strip():
                        s = val.strip()
                        hints.append(s)
                        # 同时加文件名片段，适配 contexts 中只保留文件名的情况
                        if "/" in s:
                            hints.append(s.rsplit("/", 1)[-1])
        except Exception:
            return hints

        # 去重并限制数量
        uniq = []
        for h in hints:
            if h not in uniq:
                uniq.append(h)
        return uniq[:8]

    def _is_image_event(self, event: AstrMessageEvent) -> bool:
        """判断当前事件是否包含图片组件。"""
        if not hasattr(event, "get_messages"):
            return False
        try:
            for comp in event.get_messages() or []:
                ctype = str(getattr(comp, "type", "")).lower()
                cname = comp.__class__.__name__.lower()
                if "image" in ctype or "image" in cname:
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _build_image_turn_recall_query(
        user_text: str | None, image_caption: str | None
    ) -> str:
        """图片轮次召回查询：优先文本+图片转述，缺失转述时回退纯文本。"""
        caption = (image_caption or "").strip()

        text = (user_text or "").strip()
        text = re.sub(r"<image_caption>.*?</image_caption>", "", text, flags=re.DOTALL)
        text = re.sub(r"<picture>.*?</picture>", "", text, flags=re.DOTALL)
        text = re.sub(r"<system_reminder>.*?</system_reminder>", "", text, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", text).strip()

        if caption:
            if text:
                # 避免重复拼接相同内容
                if caption in text:
                    return text
                return f"{text}\n图片描述：{caption}"
            return caption

        return text

    def _log_recall_request_shape(
        self, session_id: str, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """输出召回前请求形态，辅助定位图片消息为何未进入 prompt。"""
        try:
            prompt = getattr(req, "prompt", None)
            prompt_len = len(prompt) if isinstance(prompt, str) else 0
            has_prompt = bool(isinstance(prompt, str) and prompt.strip())

            contexts = getattr(req, "contexts", None)
            contexts_count = len(contexts) if isinstance(contexts, list) else 0
            extra_text_parts = self._collect_extra_user_text_parts(req)
            extra_count = len(extra_text_parts)
            extra_has_caption = any(
                "<image_caption>" in p and "</image_caption>" in p
                for p in extra_text_parts
            )
            extra_has_image_attachment = any(
                "[Image Attachment:" in p for p in extra_text_parts
            )
            extra_preview = " | ".join(
                p[:80].replace("\n", " ") for p in extra_text_parts[:2]
            )

            event_text = event.get_message_str() or ""
            event_text_len = len(event_text.strip())
            component_types = []
            if hasattr(event, "get_messages"):
                try:
                    component_types = [
                        getattr(comp, "type", comp.__class__.__name__)
                        for comp in (event.get_messages() or [])
                    ]
                except Exception:
                    component_types = []
            image_hints = self._extract_event_image_hints(event)

            tail_role = ""
            tail_content_type = ""
            tail_text_len = 0
            tail_has_image_caption = False
            tail_text_preview = ""

            if isinstance(contexts, list) and contexts:
                tail = contexts[-1]
                if isinstance(tail, dict):
                    tail_role = str(tail.get("role", ""))
                    content = tail.get("content")
                    tail_content_type = type(content).__name__
                    if isinstance(content, str):
                        tail_text_len = len(content.strip())
                        tail_has_image_caption = (
                            "<image_caption>" in content
                            and "</image_caption>" in content
                        )
                        tail_text_preview = content[:120].replace("\n", " ")
                    elif isinstance(content, list):
                        text_parts = []
                        for part in content:
                            if (
                                isinstance(part, dict)
                                and part.get("type") == "text"
                                and isinstance(part.get("text"), str)
                            ):
                                text_parts.append(part["text"])
                        if text_parts:
                            joined = "\n".join(text_parts)
                            tail_text_len = len(joined.strip())
                            tail_has_image_caption = (
                                "<image_caption>" in joined
                                and "</image_caption>" in joined
                            )
                            tail_text_preview = joined[:120].replace("\n", " ")

            logger.info(
                f"[{session_id}] [RecallShape] prompt_has={has_prompt}, prompt_len={prompt_len}, "
                f"contexts_count={contexts_count}, event_text_len={event_text_len}, "
                f"event_components={component_types}, image_hints={image_hints}, "
                f"extra_parts_count={extra_count}, extra_has_caption={extra_has_caption}, "
                f"extra_has_image_attachment={extra_has_image_attachment}, "
                f"extra_preview='{extra_preview}'"
            )
            if contexts_count > 0:
                logger.info(
                    f"[{session_id}] [RecallShape] tail_role={tail_role or 'unknown'}, "
                    f"tail_content_type={tail_content_type or 'unknown'}, "
                    f"tail_text_len={tail_text_len}, tail_has_image_caption={tail_has_image_caption}, "
                    f"tail_text_preview='{tail_text_preview}'"
                )

                # 再额外输出“最近一条 user 消息”的结构，避免只看到 tail=assistant
                for idx in range(len(contexts) - 1, -1, -1):
                    item = contexts[idx]
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("role", "")).lower()
                    if role != "user":
                        continue

                    content = item.get("content")
                    user_content_type = type(content).__name__
                    user_has_caption = False
                    user_text_preview = ""
                    user_part_types = []

                    if isinstance(content, str):
                        user_has_caption = (
                            "<image_caption>" in content and "</image_caption>" in content
                        )
                        user_text_preview = content[:120].replace("\n", " ")
                    elif isinstance(content, list):
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict):
                                ptype = str(part.get("type", "unknown"))
                                user_part_types.append(ptype)
                                if ptype == "text" and isinstance(part.get("text"), str):
                                    text_parts.append(part["text"])
                        if text_parts:
                            joined = "\n".join(text_parts)
                            user_has_caption = (
                                "<image_caption>" in joined and "</image_caption>" in joined
                            )
                            user_text_preview = joined[:120].replace("\n", " ")

                    logger.info(
                        f"[{session_id}] [RecallShape] latest_user_index={idx}, "
                        f"user_content_type={user_content_type}, user_part_types={user_part_types}, "
                        f"user_has_image_caption={user_has_caption}, user_text_preview='{user_text_preview}'"
                    )
                    break
        except Exception as e:
            logger.debug(f"[{session_id}] 输出 RecallShape 诊断日志失败: {e}")

    def _prepare_user_message_for_storage(
        self, content: str, enable_image_caption: bool = True
    ) -> str:
        """清理系统标签并将图片描述规范化写入会话消息。"""
        text = content or ""
        caption = self._extract_image_caption(text)

        # 清理系统标签与图片描述标签，仅保留用户可读文本
        text = re.sub(r"<system_reminder>.*?</system_reminder>", "", text, flags=re.DOTALL)
        text = re.sub(r"<image_caption>.*?</image_caption>", "", text, flags=re.DOTALL)
        text = re.sub(r"<picture>.*?</picture>", "", text, flags=re.DOTALL)
        text = re.sub(r"\[Image Attachment:[^\]]+\]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if caption and enable_image_caption:
            # 已有图片转述时，去掉占位符，避免展示成 “[图片] <picture>...”
            text = re.sub(r"\[图片\]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return f"{text}\n\n<picture>{caption}</picture>"
            return f"<picture>{caption}</picture>"

        return text

    async def _patch_recent_group_image_message(
        self, session_id: str, image_caption: str
    ) -> bool:
        """
        为群聊最近的图片占位消息补写图片描述。

        说明：
        - 只尝试修正最近窗口内的用户消息，避免误改历史。
        - 仅在包含图片占位符且尚未带图片描述时执行。
        """
        if (
            not self.conversation_manager
            or not self.conversation_manager.store
            or not self.conversation_manager.store.connection
        ):
            return False
        if not self.config_manager.get("session_manager.capture_image_caption", True):
            return False
        if not image_caption:
            return False

        now = time.time()
        recent_messages = await self.conversation_manager.get_messages(
            session_id=session_id, limit=20
        )

        target = None
        for msg in reversed(recent_messages):
            if msg.role != "user":
                continue
            if now - float(msg.timestamp or 0) > 20:
                # 再往前通常已不是本次请求对应消息
                break

            content = msg.content or ""
            if (
                ("<picture>" in content and "</picture>" in content)
                or ("图片描述" in content)
            ):
                continue
            if "[图片]" not in content and "[Image Attachment:" not in content:
                continue

            target = msg
            break

        if not target:
            return False

        base = (target.content or "").strip()
        # 已补写图片转述时，移除图片占位符
        base = re.sub(r"\[Image Attachment:[^\]]+\]", "", base)
        base = re.sub(r"\[图片\]", "", base)
        base = re.sub(r"\s+", " ", base).strip()
        if base:
            new_content = f"{base}\n\n<picture>{image_caption}</picture>"
        else:
            new_content = f"<picture>{image_caption}</picture>"

        await self.conversation_manager.store.connection.execute(
            """
            UPDATE messages
            SET content = ?
            WHERE id = ? AND session_id = ?
            """,
            (new_content, target.id, session_id),
        )
        await self.conversation_manager.store.connection.commit()

        logger.debug(
            f"[{session_id}] 已为群聊消息补写图片描述: message_id={target.id}, "
            f"caption_len={len(image_caption)}"
        )
        return True

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

            logger.info(
                f"[{session_id}] 消息清理完成: "
                f"删除={actually_deleted}条, 剩余={new_actual_count}条, "
                f"总结索引: {last_summarized_index} -> {new_summarized_index}"
            )

        except Exception as e:
            logger.error(f"[{session_id}] 删除旧消息失败: {e}", exc_info=True)

    async def shutdown(self):
        """关闭事件处理器，等待所有存储任务完成"""
        self._shutting_down = True
        if self._storage_tasks:
            logger.info(f"等待 {len(self._storage_tasks)} 个存储任务完成...")
            await asyncio.gather(*self._storage_tasks, return_exceptions=True)
            self._storage_tasks.clear()
        self._sessions_summarizing.clear()
        self._sessions_summary_rerun_requested.clear()
        logger.info("EventHandler 已关闭")
