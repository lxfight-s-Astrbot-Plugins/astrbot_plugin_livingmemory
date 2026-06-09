"""
记忆反思模块
负责检查是否需要记忆总结和存储
"""

import asyncio
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from astrbot.api.provider import LLMResponse

from ..utils import get_persona_id

if TYPE_CHECKING:
    from ..base.config_manager import ConfigManager
    from ..managers.conversation_manager import ConversationManager
    from ..managers.memory_engine import MemoryEngine
    from ..processors.memory_processor import MemoryProcessor
    from .message_utils import MessageUtils


class MemoryReflection:
    """记忆反思类"""

    def __init__(
        self,
        context: Any,
        config_manager: "ConfigManager",
        memory_engine: "MemoryEngine",
        memory_processor: "MemoryProcessor",
        conversation_manager: "ConversationManager",
        message_utils: "MessageUtils",
        storage_tasks: set[asyncio.Task],
        storage_sessions_inflight: set[str],
        storage_state_lock: asyncio.Lock,
    ):
        """
        初始化记忆反思模块

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            memory_processor: 记忆处理器
            conversation_manager: 会话管理器
            message_utils: 消息处理工具
            storage_tasks: 后台存储任务集合（共享状态）
            storage_sessions_inflight: 正在处理的会话集合（共享状态）
            storage_state_lock: 存储状态锁（共享状态）
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.conversation_manager = conversation_manager
        self.message_utils = message_utils
        self._storage_tasks = storage_tasks
        self._storage_sessions_inflight = storage_sessions_inflight
        self._storage_state_lock = storage_state_lock
        self._shutting_down = False

    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """Check if reflection and memory storage is needed after LLM response"""
        logger.debug(
            f"[DEBUG-Reflection] 进入 handle_memory_reflection，resp.role={resp.role}"
        )

        if resp.role != "assistant":
            return

        # 过滤 tool 循环中间轮次（有工具调用时跳过，等待最终总结轮）
        if resp.tools_call_name:
            logger.debug(
                f"[DEBUG-Reflection] 检测到工具调用响应（tools={resp.tools_call_name}），跳过记录"
            )
            return

        # 过滤 tool 循环最终总结：若本次响应是 tool 调用完成后的总结，
        # 其 tools_call_extra_content 会携带工具调用上下文，说明这是 tool loop 产生的内容
        if resp.tools_call_extra_content:
            logger.debug(
                "[DEBUG-Reflection] 检测到 tool loop 总结响应（tools_call_extra_content 非空），跳过记录"
            )
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

            # 私聊：助手消息写入后也执行消息数量上限控制
            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if not is_group:
                await self.message_utils.enforce_message_limit(session_id)

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
            # 这种情况通常发生在消息被删除后
            if last_summarized_index > total_messages:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] last_summarized_index({last_summarized_index}) "
                    f"> 实际消息数({total_messages})，调整为当前消息总数"
                )
                # 调整为当前消息总数，而非归零（避免重复处理已总结的内容）
                last_summarized_index = total_messages
                await self.conversation_manager.update_session_metadata(
                    session_id, "last_summarized_index", total_messages
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

                # 创建后台任务进行存储（跟踪任务）
                if not self._shutting_down:
                    async with self._storage_state_lock:
                        if session_id in self._storage_sessions_inflight:
                            logger.info(
                                f"[{session_id}] 已有记忆反思任务在执行，跳过本次触发"
                            )
                            return
                        self._storage_sessions_inflight.add(session_id)

                    try:
                        task = asyncio.create_task(
                            self._storage_task(
                                session_id,
                                history_messages,
                                persona_id,
                                start_index,
                                end_index,
                                retry_count,
                            )
                        )
                    except Exception:
                        self._storage_sessions_inflight.discard(session_id)
                        raise

                    self._storage_tasks.add(task)
                    task.add_done_callback(
                        lambda t, sid=session_id: self._on_storage_task_done(t, sid)
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理记忆反思时发生错误: {e}", exc_info=True)

    async def _storage_task(
        self,
        session_id: str,
        history_messages: list[dict],
        persona_id: str,
        start_index: int,
        end_index: int,
        retry_count: int,
    ):
        """后台存储任务"""
        from ..utils import OperationContext

        async with OperationContext("记忆存储", session_id):
            try:
                # 如果其他任务已经推进了总结进度，本任务可能已过期，直接跳过
                current_summarized = (
                    await self.conversation_manager.get_session_metadata(
                        session_id, "last_summarized_index", 0
                    )
                )
                try:
                    summarized_index = int(current_summarized)
                except (TypeError, ValueError):
                    summarized_index = 0

                if summarized_index >= end_index:
                    logger.info(
                        f"[{session_id}] 检测到过期总结任务，跳过: "
                        f"current={summarized_index}, target_end={end_index}"
                    )
                    return

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
                    (
                        content,
                        metadata,
                        importance,
                    ) = await self.memory_processor.process_conversation(
                        messages=history_messages,
                        is_group_chat=is_group_chat,
                        persona_id=persona_id,
                    )

                    atoms = self.memory_processor.classify_atoms_from_metadata(
                        metadata=metadata,
                        parent_importance=importance,
                        session_id=session_id,
                        persona_id=persona_id,
                    )

                    # 补充 source_window 元数据，记录本次总结的消息范围
                    metadata["source_window"] = {
                        "session_id": session_id,
                        "start_index": start_index,
                        "end_index": end_index,
                        "message_count": end_index - start_index,
                    }

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
                        atoms=atoms,
                    )

                    logger.info(
                        f"[{session_id}] 成功存储对话记忆（{len(history_messages)}条消息，重要性={importance:.2f}）"
                    )

                # 成功：更新已总结的位置，清除待处理记录
                if self.conversation_manager:
                    try:
                        await self.conversation_manager.update_session_metadata(
                            session_id, "last_summarized_index", end_index
                        )
                        await self.conversation_manager.update_session_metadata(
                            session_id, "pending_summary", None
                        )
                        logger.info(
                            f"[{session_id}] 更新滑动窗口位置: last_summarized_index = {end_index}"
                        )
                    except Exception as meta_err:
                        logger.error(
                            f"[{session_id}] 记忆已存储但元数据更新失败: {meta_err}。"
                            "下次触发时将跳过本段消息，避免重复总结。",
                            exc_info=True,
                        )
                        # Advance the index anyway to prevent re-processing the
                        # same message range (memory is already stored durably).
                        try:
                            await self.conversation_manager.update_session_metadata(
                                session_id, "last_summarized_index", end_index
                            )
                            await self.conversation_manager.update_session_metadata(
                                session_id, "pending_summary", None
                            )
                        except Exception:
                            logger.error(
                                f"[{session_id}] 重试元数据更新仍然失败，"
                                "可能出现重复总结。",
                                exc_info=True,
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
        """记录待处理的失败总结信息"""
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

    def _on_storage_task_done(self, task: asyncio.Task, session_id: str):
        """存储任务完成回调"""
        self._storage_tasks.discard(task)
        self._storage_sessions_inflight.discard(session_id)

        if task.cancelled():
            logger.info(f"[{session_id}] 存储任务已取消")
            return

        exc = task.exception()
        if exc:
            logger.error(f"[{session_id}] 存储任务异常: {exc}", exc_info=exc)

    def set_shutting_down(self, value: bool):
        """设置关闭状态"""
        self._shutting_down = value
