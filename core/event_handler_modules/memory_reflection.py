"""
记忆反思模块
负责检查是否需要记忆总结和存储
"""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse

from ..memory_scope import (
    is_event_memory_allowed,
    is_suspicious_session_id,
    resolve_event_memory_access,
)
from .memory_reflection_storage import MemoryReflectionStorageMixin

_DEFAULT_MEMORY_SCOPE = object()

# 后台反思任务的全局并发上限：避免多会话同时爆发时齐刷刷调用 LLM/写嵌入。
# 仅限制反思总结（LLM + 记忆写入）阶段，不限制消息入库。
_MAX_CONCURRENT_REFLECTIONS = 2

# 长空闲补总结阈值：距上一条消息超过该时长时，下一轮对话立即补一次总结，
# 不再等 summary_trigger_rounds 轮（避免长时间不聊导致这次对话永久漏总结）。
_IDLE_SUMMARY_GAP_SECONDS = 60 * 60


def _message_timestamp(message: Any) -> float | None:
    """读取已入库消息的时间戳；缺失或非法时返回 None（该相邻对不参与切分）。"""
    value = getattr(message, "timestamp", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    return timestamp


def cancel_session_storage_task(
    session_tasks: dict[str, asyncio.Task], session_id: str
) -> bool:
    """取消指定会话进行中的后台记忆总结任务。

    会话历史被回滚（WebUI 编辑/重试）时调用：任务持有的消息范围已失效，
    取消可避免它继续调用 LLM 并写入过期内容。修订号校验仍是最终防线
    （取消在 LLM 调用期间可能延迟生效）。
    """
    task = session_tasks.get(session_id)
    if task is None or task.done():
        return False
    task.cancel()
    logger.info(f"[{session_id}] 会话历史已回滚，取消进行中的记忆总结任务")
    return True


async def _mark_idle_summary_if_needed(manager: Any, session_id: str) -> bool:
    """长空闲后标记“下一轮补总结”，返回是否写入标记。

    Args:
        manager: ConversationManager；会话元数据与最近消息的读取方。
        session_id: 会话 ID。

    Returns:
        bool: 本次是否新写入 ``idle_summary_pending=True``。
    """
    try:
        messages = await manager.get_messages(session_id, limit=2)
    except Exception as exc:
        logger.debug(f"[{session_id}] 读取最近消息失败，跳过空闲检测: {exc}")
        return False
    if len(messages) < 2:
        return False
    previous_timestamp = _message_timestamp(messages[-2])
    current_timestamp = _message_timestamp(messages[-1])
    if previous_timestamp is None or current_timestamp is None:
        return False
    gap_seconds = current_timestamp - previous_timestamp
    if gap_seconds < _IDLE_SUMMARY_GAP_SECONDS:
        return False
    await manager.update_session_metadata(session_id, "idle_summary_pending", True)
    logger.info(
        f"[{session_id}] 距上一条消息 {gap_seconds / 60:.0f} 分钟，已标记下一轮补总结"
    )
    return True


@dataclass(frozen=True, slots=True)
class _ReflectionPlan:
    """一次反思总结的不可变计划，供后台存储任务消费。"""

    session_id: str
    history_messages: list[Any]
    persona_id: Any
    start_index: int
    end_index: int
    retry_count: int
    memory_scope: str
    expected_revision: int | None


if TYPE_CHECKING:
    from ..base.config_manager import ConfigManager
    from ..managers.conversation_manager import ConversationManager
    from ..managers.memory_engine import MemoryEngine
    from ..processors.memory_processor import MemoryProcessor
    from .message_utils import MessageUtils


class MemoryReflection(MemoryReflectionStorageMixin):
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
        consolidation_manager=None,
        storage_session_tasks: dict[str, asyncio.Task] | None = None,
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
            storage_session_tasks: 会话 -> 进行中任务映射（共享状态，用于回滚取消）
            consolidation_manager: 记忆整合管理器（用于反思触发）
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
        # 会话 -> 进行中的总结任务：会话回滚时按会话精准取消（共享状态）
        self._storage_session_tasks: dict[str, asyncio.Task] = (
            storage_session_tasks if storage_session_tasks is not None else {}
        )
        self.consolidation_manager = consolidation_manager
        self._shutting_down = False
        # 全局反思并发额度：跨会话共享，限制同时进行的 LLM 总结数量
        self._reflection_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REFLECTIONS)

    def _schedule_consolidation(self) -> None:
        """反思触发时在后台顺带检查记忆整合（带冷却，不会频繁执行）。"""
        if self.consolidation_manager is None or self._shutting_down:
            return
        task = asyncio.create_task(self.consolidation_manager.maybe_run("reflection"))
        self._storage_tasks.add(task)
        task.add_done_callback(self._on_auxiliary_task_done)

    def _on_auxiliary_task_done(self, task: asyncio.Task) -> None:
        """取回后台任务异常并记录，避免静默失败。"""
        self._storage_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[MemoryReflection] Background task failed: {exc}", exc_info=exc
            )

    @staticmethod
    def _find_window_split_boundary(
        messages: list[Any],
        *,
        split_by_day: bool,
        gap_minutes: int,
        min_segment_messages: int = 2,
    ) -> tuple[int, str] | None:
        """在候选消息中寻找最早的总结窗口切分边界（本地日期变化或空闲间隔）。

        Args:
            messages: ``[start_index, end_index)`` 范围内的消息，按入库顺序排列。
            split_by_day: 相邻消息的本地日历日期不同时切分。
            gap_minutes: 相邻消息时间差超过该分钟数时切分；``<= 0`` 表示禁用。
            min_segment_messages: 边界前最老片段至少需要的消息数。更早的边界会被
                跳过，否则保留的“不足一轮跳过”逻辑会让该会话永远无法推进。

        Returns:
            ``(最老片段的条数, 原因)``，原因为 ``"day"`` 或 ``"gap"``；
            没有可用边界时返回 ``None``（保持原有整段总结行为）。
        """
        if len(messages) <= min_segment_messages:
            return None

        timestamps = [_message_timestamp(message) for message in messages]
        gap_seconds = gap_minutes * 60 if gap_minutes > 0 else 0
        for index in range(min_segment_messages, len(messages)):
            previous = timestamps[index - 1]
            current = timestamps[index]
            # 时间戳缺失或乱序（后一条更早）时保守不切分
            if previous is None or current is None or current < previous:
                continue
            if split_by_day and (
                datetime.fromtimestamp(previous).date()
                != datetime.fromtimestamp(current).date()
            ):
                return index, "day"
            if gap_seconds and current - previous > gap_seconds:
                return index, "gap"
        return None

    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """Check if reflection and memory storage is needed after LLM response."""
        logger.debug(
            f"[DEBUG-Reflection] 进入 handle_memory_reflection，resp.role={resp.role}"
        )
        try:
            prepared = await self._reflection_precheck(event, resp)
            if prepared is None:
                return
            session_id, response_text = prepared
            plan = await self._reflection_plan(event, session_id, response_text)
            if plan is None:
                return
            await self._schedule_reflection(plan)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理记忆反思时发生错误: {e}", exc_info=True)

    async def _reflection_precheck(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> tuple[str, str] | None:
        """过滤不需要反思的响应；返回 (session_id, response_text) 表示可以继续。

        工具调用轮次的助手文字由本方法单独写入会话库（不触发后台总结），
        因此这类轮次也返回 None。
        """
        if not is_event_memory_allowed(self.config_manager, event):
            logger.debug("当前事件不在记忆白名单中，跳过记忆反思")
            return None

        # 流式增量只是完整回复的片段，不能作为一轮对话入库；
        # Mock 对象没有 is_chunk 属性，按 False 处理，仅真正为 True 时跳过
        if getattr(resp, "is_chunk", False) is True:
            return None

        # 工具调用轮次的 role 会被 OpenAI 兼容 Provider 改写为 "tool"，
        # 这些轮次的助手文字仍需入库；其他 role（如 err）不记录
        if resp.role not in ("assistant", "tool"):
            return None

        session_id = event.unified_msg_origin
        logger.debug(f"[DEBUG-Reflection] 获取到 unified_msg_origin: {session_id}")
        if not session_id:
            logger.warning("[DEBUG-Reflection] session_id 为空，跳过反思")
            return None

        if is_suspicious_session_id(session_id):
            logger.warning(
                f"[{session_id}] 检测到异常的session_id，这可能导致记忆总结异常。"
            )

        # 检查响应内容是否有效（过滤空回复和错误）
        response_text = resp.completion_text
        if not response_text or not response_text.strip():
            logger.debug(f"[{session_id}] 模型返回空回复，跳过记录")
            return None

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
            return None

        if await self._is_superseded_response(event, session_id):
            return None

        # 长空闲后的下一轮：标记补总结，_reflection_plan 会把触发阈值降为 1
        await _mark_idle_summary_if_needed(self.conversation_manager, session_id)

        # 工具轮：tool loop 尚未结束，总结会截断在工具调用中间，
        # 因此只把助手文字写入会话库，最终回复轮才触发后台总结。
        if resp.role == "tool" or resp.tools_call_name or resp.tools_call_extra_content:
            logger.debug(f"[{session_id}] 工具调用轮次仅记录助手消息，不触发记忆总结")
            await self.conversation_manager.add_message_from_event(
                event=event,
                role="assistant",
                content=response_text,
            )
            await self.message_utils.enforce_message_limit(session_id)
            return None

        return session_id, response_text

    async def _is_superseded_response(
        self, event: AstrMessageEvent, session_id: str
    ) -> bool:
        """WebUI 编辑/重试防护：本回复属于已被取代或重复的轮次时返回 True。

        库尾为 assistant 且 checkpoint 不同的情况（如纯图片轮次的回复紧跟在前
        一轮回复之后）不属于被取代轮次；真正迟到的孤立回复由 on_llm_request
        对账在下次请求时清理。
        """
        checkpoint_id = None
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra):
            try:
                checkpoint_id = get_extra("llm_checkpoint_id")
            except Exception as exc:
                checkpoint_id = None
                logger.debug(f"Failed to read llm_checkpoint_id from event: {exc}")
        if not (isinstance(checkpoint_id, str) and checkpoint_id):
            return False

        try:
            last_message = await self.conversation_manager.get_last_message(session_id)
        except Exception as exc:
            logger.debug(f"[{session_id}] 读取最后一条消息失败，跳过守卫: {exc}")
            return False
        if last_message is None:
            return False

        last_metadata = last_message.metadata or {}
        last_checkpoint = last_metadata.get("llm_checkpoint_id")
        if not (isinstance(last_checkpoint, str) and last_checkpoint):
            return False

        if last_message.role == "user" and last_checkpoint != checkpoint_id:
            # 被取代的回复会被丢弃而不入库：用 warning 暴露 checkpoint 漂移
            # （旧->新），避免平台时间戳异常时助手消息被静默丢掉（#289）
            logger.warning(
                f"[{session_id}] 检测到被取代的回复"
                f"（checkpoint {last_checkpoint} -> {checkpoint_id}），跳过存储"
            )
            return True
        if last_message.role == "assistant" and last_checkpoint == checkpoint_id:
            logger.debug(
                f"[{session_id}] 检测到重复回复（checkpoint {checkpoint_id}），跳过存储"
            )
            return True
        return False

    async def _reflection_plan(
        self, event: AstrMessageEvent, session_id: str, response_text: str
    ) -> _ReflectionPlan | None:
        """写入助手消息、判断触发条件并计算本次总结窗口。"""
        await self.conversation_manager.add_message_from_event(
            event=event,
            role="assistant",
            content=response_text,
        )
        logger.debug(f"[DEBUG-Reflection] [{session_id}] 已添加助手响应消息")

        await self.message_utils.enforce_message_limit(session_id)

        # Read the retained history length used by the reflection cursor.
        actual_message_count = await self.conversation_manager.store.get_message_count(
            session_id
        )

        # 在读取总结范围之前捕获会话修订号：范围计算与后台任务启动之间
        # 若发生 WebUI 编辑/重试回滚，任务必须整体失效（否则会用回滚前的
        # 消息范围写入已撤销内容）
        expected_revision = await self._read_session_revision(session_id)

        total_messages = actual_message_count
        trigger_rounds = self.config_manager.get(
            "reflection_engine.summary_trigger_rounds", 10
        )
        # 长空闲后的补总结标记：本次只要有未总结轮次就触发，不改变正常阈值逻辑
        idle_summary_pending = bool(
            await self.conversation_manager.get_session_metadata(
                session_id, "idle_summary_pending", False
            )
        )
        if idle_summary_pending:
            trigger_rounds = 1
        last_summarized_index = await self.conversation_manager.get_session_metadata(
            session_id, "last_summarized_index", 0
        )

        # 消息被删除后游标可能超出实际消息数；调整为当前总数，而非归零
        # （避免重复处理已总结的内容）
        if last_summarized_index > total_messages:
            logger.warning(
                f"[DEBUG-Reflection] [{session_id}] last_summarized_index({last_summarized_index}) "
                f"> 实际消息数({total_messages})，调整为当前消息总数"
            )
            last_summarized_index = total_messages
            await self.conversation_manager.update_session_metadata(
                session_id, "last_summarized_index", total_messages
            )

        unsummarized_rounds = (total_messages - last_summarized_index) // 2
        pending_summary = await self.conversation_manager.get_session_metadata(
            session_id, "pending_summary", None
        )
        logger.info(
            f"[DEBUG-Reflection] [{session_id}] 总消息数: {total_messages}, "
            f"上次总结位置: {last_summarized_index}, "
            f"未总结轮数: {unsummarized_rounds}, "
            f"触发阈值: {trigger_rounds}轮, "
            f"空闲补总结: {idle_summary_pending}, "
            f"待处理失败总结: {pending_summary is not None}"
        )
        if unsummarized_rounds < trigger_rounds:
            return None

        logger.info(
            f"[{session_id}] 未总结轮数达到 {unsummarized_rounds} 轮，启动记忆反思任务"
        )
        return await self._build_reflection_window(
            event,
            session_id,
            total_messages,
            last_summarized_index,
            pending_summary,
            expected_revision,
        )

    async def _build_reflection_window(
        self,
        event: AstrMessageEvent,
        session_id: str,
        total_messages: int,
        last_summarized_index: int,
        pending_summary: dict | None,
        expected_revision: int | None,
    ) -> _ReflectionPlan | None:
        """首次触发总结：解析人格/作用域后构造最老片段计划。"""
        access = await resolve_event_memory_access(
            self.config_manager, self.context, event
        )
        # 群聊开启频道记忆后，总结进入群共享的频道作用域；私聊不变。
        memory_scope = access.channel_scope or access.scope or session_id
        return await self._build_reflection_plan_for_cursor(
            session_id,
            total_messages,
            last_summarized_index,
            pending_summary,
            expected_revision,
            access.persona_id,
            memory_scope,
        )

    async def _build_reflection_plan_for_cursor(
        self,
        session_id: str,
        total_messages: int,
        last_summarized_index: int,
        pending_summary: dict | None,
        expected_revision: int | None,
        persona_id: Any,
        memory_scope: str,
    ) -> _ReflectionPlan | None:
        """处理待重试范围、窗口切分并取回总结输入消息。"""
        max_messages = self.config_manager.get(
            "reflection_engine.max_messages_per_segment", 120
        )
        (
            start_index,
            candidate_messages,
        ) = await self.conversation_manager.get_summary_window(session_id, max_messages)
        if len(candidate_messages) < 2:
            return None
        end_index = start_index + len(candidate_messages)
        retry_count = pending_summary.get("retry_count", 0) if pending_summary else 0

        # 窗口切分配置：跨天或空闲间隔超过阈值时从最老片段开始逐段总结，
        # 避免一整段跨天/跨长空闲的对话被合并进同一条记忆。
        split_by_day = bool(
            self.config_manager.get("reflection_engine.window_split_by_day", True)
        )
        try:
            gap_minutes = int(
                self.config_manager.get(
                    "reflection_engine.window_split_gap_minutes", 180
                )
            )
        except (TypeError, ValueError):
            gap_minutes = 180

        split = self._find_window_split_boundary(
            candidate_messages,
            split_by_day=split_by_day,
            gap_minutes=gap_minutes,
        )
        if split is not None:
            split_offset, split_reason = split
            end_index = start_index + split_offset
            reason_text = "日期变化" if split_reason == "day" else "空闲间隔"
            logger.info(
                f"[{session_id}] 总结窗口按{reason_text}在消息 {end_index} 处切分："
                f"本次总结片段 [{start_index}:{end_index}]，"
                f"后续片段在同一任务内继续消费"
            )

        messages_to_summarize = end_index - start_index
        rounds_to_summarize = messages_to_summarize // 2
        logger.info(
            f"[{session_id}] 滑动窗口总结: "
            f"消息范围 [{start_index}:{end_index}]/{total_messages}, "
            f"本次总结 {rounds_to_summarize} 轮"
        )
        history_messages = candidate_messages[:messages_to_summarize]
        if retry_count >= 3:
            logger.warning(
                f"[{session_id}] Abandoning failed summary segment [{start_index}:{end_index}]"
            )
            await self.conversation_manager.commit_summary_cursor(
                session_id, history_messages[-1], expected_revision
            )
            return None

        logger.info(f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结")

        return _ReflectionPlan(
            session_id=session_id,
            history_messages=history_messages,
            persona_id=persona_id,
            start_index=start_index,
            end_index=end_index,
            retry_count=retry_count,
            memory_scope=memory_scope,
            expected_revision=expected_revision,
        )

    async def _build_next_reflection_plan(
        self,
        session_id: str,
        persona_id: Any,
        memory_scope: str,
        expected_revision: int | None,
    ) -> _ReflectionPlan | None:
        """读取成功提交后的最新游标，为积压消费构造下一段计划。"""
        if not await self._revision_still_valid(session_id, expected_revision):
            return None

        total_messages = await self.conversation_manager.store.get_message_count(
            session_id
        )
        last_summarized_index = await self.conversation_manager.get_session_metadata(
            session_id, "last_summarized_index", 0
        )
        pending_summary = await self.conversation_manager.get_session_metadata(
            session_id, "pending_summary", None
        )
        if pending_summary is not None:
            return None
        if total_messages - last_summarized_index < 2:
            return None

        return await self._build_reflection_plan_for_cursor(
            session_id,
            total_messages,
            last_summarized_index,
            pending_summary,
            expected_revision,
            persona_id,
            memory_scope,
        )

    async def _run_reflection_task(self, plan: _ReflectionPlan) -> None:
        """获取全局并发额度后执行反思任务。

        信号量用 ``async with`` 管理，任务被取消或总结抛异常时都会自动释放
        额度，不会出现额度泄漏导致后续反思永久排队。
        """
        async with self._reflection_semaphore:
            await self._consume_reflection_segments(plan)

    async def _consume_reflection_segments(self, plan: _ReflectionPlan) -> None:
        """在同一后台任务内逐段消费积压，直到清空或达到单次片段预算。"""
        committed_any = False
        max_segments = self.config_manager.get(
            "reflection_engine.max_segments_per_run", 10
        )
        for segment_index in range(max_segments):
            committed = await self._storage_task(
                plan.session_id,
                plan.history_messages,
                plan.persona_id,
                plan.start_index,
                plan.end_index,
                plan.retry_count,
                memory_scope=plan.memory_scope,
                expected_revision=plan.expected_revision,
            )
            if not committed:
                break
            committed_any = True
            # 总结成功提交后清除空闲补总结标记，恢复正常触发阈值
            if await self.conversation_manager.get_session_metadata(
                plan.session_id, "idle_summary_pending", False
            ):
                await self._commit_session_metadata(
                    plan.session_id,
                    "idle_summary_pending",
                    False,
                    plan.expected_revision,
                )
            if self._shutting_down:
                break

            if segment_index + 1 >= max_segments:
                logger.debug(
                    f"[{plan.session_id}] 达到单次反思片段上限 {max_segments}，"
                    "停止继续消费"
                )
                break

            next_plan = await self._build_next_reflection_plan(
                plan.session_id,
                plan.persona_id,
                plan.memory_scope,
                plan.expected_revision,
            )
            if next_plan is None:
                break
            plan = next_plan
        if committed_any:
            self._schedule_consolidation()

    async def _schedule_reflection(self, plan: _ReflectionPlan) -> None:
        """创建并跟踪后台总结任务；同一会话已有任务在跑时跳过。"""
        if self._shutting_down:
            return
        async with self._storage_state_lock:
            if plan.session_id in self._storage_sessions_inflight:
                logger.info(f"[{plan.session_id}] 已有记忆反思任务在执行，跳过本次触发")
                return
            self._storage_sessions_inflight.add(plan.session_id)

        try:
            task = asyncio.create_task(self._run_reflection_task(plan))
        except Exception:
            self._storage_sessions_inflight.discard(plan.session_id)
            raise

        self._storage_tasks.add(task)
        self._storage_session_tasks[plan.session_id] = task
        task.add_done_callback(
            lambda t, sid=plan.session_id: self._on_storage_task_done(t, sid)
        )

    def set_shutting_down(self, value: bool):
        """设置关闭状态"""
        self._shutting_down = value
