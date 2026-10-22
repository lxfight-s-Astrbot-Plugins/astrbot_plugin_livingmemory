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
from astrbot.api.platform import MessageType
from astrbot.api.provider import LLMResponse

from ..memory_scope import (
    is_event_memory_allowed,
    is_suspicious_session_id,
    resolve_memory_scope,
)
from ..memory_source import serialize_source_messages
from ..utils import get_persona_id

_DEFAULT_MEMORY_SCOPE = object()


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

    def _schedule_consolidation(self) -> None:
        """反思触发时在后台顺带检查记忆整合（带冷却，不会频繁执行）。"""
        if self.consolidation_manager is None or self._shutting_down:
            return
        task = asyncio.create_task(self.consolidation_manager.maybe_run("reflection"))
        self._storage_tasks.add(task)
        task.add_done_callback(self._storage_tasks.discard)

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
        """过滤不需要反思的响应；返回 (session_id, response_text) 表示可以继续。"""
        if not is_event_memory_allowed(self.config_manager, event):
            logger.debug("当前事件不在记忆白名单中，跳过记忆反思")
            return None

        if resp.role != "assistant":
            return None

        # 过滤 tool 循环中间轮次（有工具调用时跳过，等待最终总结轮）
        if resp.tools_call_name:
            logger.debug(
                f"[DEBUG-Reflection] 检测到工具调用响应（tools={resp.tools_call_name}），跳过记录"
            )
            return None

        # 过滤 tool 循环最终总结：若本次响应是 tool 调用完成后的总结，
        # 其 tools_call_extra_content 会携带工具调用上下文，说明这是 tool loop 产生的内容
        if resp.tools_call_extra_content:
            logger.debug(
                "[DEBUG-Reflection] 检测到 tool loop 总结响应（tools_call_extra_content 非空），跳过记录"
            )
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
            except Exception:
                checkpoint_id = None
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
            #（旧->新），避免平台时间戳异常时助手消息被静默丢掉（#289）
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

        # 私聊：助手消息写入后也执行消息数量上限控制
        is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
        if not is_group:
            await self.message_utils.enforce_message_limit(session_id)

        session_info = await self.conversation_manager.get_session_info(session_id)
        if not session_info:
            logger.warning(
                f"[DEBUG-Reflection] [{session_id}] session_info 为 None，跳过反思"
            )
            return None

        # 获取实际消息数量（用于数据一致性检查）
        actual_message_count = (
            await self.conversation_manager.store.get_message_count(session_id)
        )

        # 在读取总结范围之前捕获会话修订号：范围计算与后台任务启动之间
        # 若发生 WebUI 编辑/重试回滚，任务必须整体失效（否则会用回滚前的
        # 消息范围写入已撤销内容）
        expected_revision = await self._read_session_revision(session_id)

        if session_info.message_count != actual_message_count:
            logger.warning(
                f"[DEBUG-Reflection] [{session_id}] 数据不一致! "
                f"sessions表记录={session_info.message_count}, "
                f"实际消息数={actual_message_count}"
            )

        total_messages = actual_message_count
        trigger_rounds = self.config_manager.get(
            "reflection_engine.summary_trigger_rounds", 10
        )
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
            f"待处理失败总结: {pending_summary is not None}"
        )
        if unsummarized_rounds < trigger_rounds:
            return None

        logger.info(
            f"[{session_id}] 未总结轮数达到 {unsummarized_rounds} 轮，启动记忆反思任务"
        )
        self._schedule_consolidation()
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
        """处理待重试范围、窗口切分并取回总结输入消息。"""
        start_index = last_summarized_index
        end_index = total_messages
        retry_count = 0

        if pending_summary:
            pending_start = pending_summary.get("start_index", start_index)
            retry_count = pending_summary.get("retry_count", 0)
            if retry_count >= 3:
                logger.warning(
                    f"[{session_id}] 待处理总结已连续失败 {retry_count} 次，放弃该范围 "
                    f"[{pending_start}:{pending_summary.get('end_index', end_index)}]"
                )
                await self.conversation_manager.update_session_metadata(
                    session_id, "pending_summary", None
                )
                await self.conversation_manager.update_session_metadata(
                    session_id, "last_summarized_index", end_index
                )
                return None
            start_index = pending_start
            logger.info(
                f"[{session_id}] 合并待处理失败总结，新范围 [{start_index}:{end_index}], "
                f"重试次数: {retry_count + 1}/3"
            )

        if end_index - start_index < 2:
            logger.debug(f"[{session_id}] 消息数不足一轮对话，跳过总结")
            return None

        # 窗口切分配置：跨天或空闲间隔超过阈值时只总结最老片段（FIFO），
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

        candidate_messages = await self.conversation_manager.get_messages_range(
            session_id=session_id,
            start_index=start_index,
            end_index=end_index,
        )
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
                f"仅总结最老片段 [{start_index}:{end_index}]，"
                f"较新片段 [{end_index}:{total_messages}] 留待后续触发"
            )

        messages_to_summarize = end_index - start_index
        rounds_to_summarize = messages_to_summarize // 2
        logger.info(
            f"[{session_id}] 滑动窗口总结: "
            f"消息范围 [{start_index}:{end_index}]/{total_messages}, "
            f"本次总结 {rounds_to_summarize} 轮"
        )
        history_messages = candidate_messages[:messages_to_summarize]
        logger.info(f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结")

        persona_id = await get_persona_id(self.context, event)
        return _ReflectionPlan(
            session_id=session_id,
            history_messages=history_messages,
            persona_id=persona_id,
            start_index=start_index,
            end_index=end_index,
            retry_count=retry_count,
            memory_scope=(
                resolve_memory_scope(self.config_manager, event) or session_id
            ),
            expected_revision=expected_revision,
        )

    async def _schedule_reflection(self, plan: _ReflectionPlan) -> None:
        """创建并跟踪后台总结任务；同一会话已有任务在跑时跳过。"""
        if self._shutting_down:
            return
        async with self._storage_state_lock:
            if plan.session_id in self._storage_sessions_inflight:
                logger.info(
                    f"[{plan.session_id}] 已有记忆反思任务在执行，跳过本次触发"
                )
                return
            self._storage_sessions_inflight.add(plan.session_id)

        try:
            task = asyncio.create_task(
                self._storage_task(
                    plan.session_id,
                    plan.history_messages,
                    plan.persona_id,
                    plan.start_index,
                    plan.end_index,
                    plan.retry_count,
                    memory_scope=plan.memory_scope,
                    expected_revision=plan.expected_revision,
                )
            )
        except Exception:
            self._storage_sessions_inflight.discard(plan.session_id)
            raise

        self._storage_tasks.add(task)
        self._storage_session_tasks[plan.session_id] = task
        task.add_done_callback(
            lambda t, sid=plan.session_id: self._on_storage_task_done(t, sid)
        )

    async def _persist_summary_memory(
        self,
        *,
        session_id: str,
        memory_scope: str,
        persona_id: Any,
        content: str,
        metadata: dict[str, Any],
        importance: float,
        atoms: list,
        source_messages: list[dict[str, Any]] | None,
    ) -> tuple[int | None, bool, float | None]:
        """Store a summary memory, merging into a near-duplicate when configured.

        Args:
            session_id: Raw conversation session id (for logging/compensation).
            memory_scope: Resolved memory scope stored in metadata.
            persona_id: Persona id for the summary.
            content: Generated summary text.
            metadata: Structured summary metadata.
            importance: Summary importance.
            atoms: Classified memory atoms.
            source_messages: Optional retained source messages.

        Returns:
            ``(memory_id, created, similarity)``. ``created`` is False when the
            summary was merged into an existing memory, so rollback compensation
            must not delete that pre-existing memory.
        """
        engine = self.memory_engine
        if engine is None:
            return None, False, None

        enabled = True
        threshold = 0.92
        limit = 3
        try:
            enabled = bool(self.config_manager.get("memory_dedup.enabled", True))
            threshold = float(
                self.config_manager.get("memory_dedup.similarity_threshold", 0.92)
                or 0.92
            )
            limit = int(
                self.config_manager.get("memory_dedup.max_candidates", 3) or 3
            )
        except (TypeError, ValueError):
            enabled, threshold, limit = True, 0.92, 3

        if enabled:
            try:
                duplicate = await engine.find_similar_memory(
                    content,
                    session_id=memory_scope,
                    persona_id=persona_id,
                    threshold=threshold,
                    limit=limit,
                )
                if duplicate is not None:
                    merged = await engine.merge_duplicate_memory(
                        int(duplicate["id"]),
                        importance=importance,
                        metadata=metadata,
                        atoms=atoms,
                        source_messages=source_messages,
                    )
                    if merged:
                        return (
                            int(duplicate["id"]),
                            False,
                            float(duplicate.get("similarity") or 0.0),
                        )
                    logger.warning(
                        f"[{session_id}] 近重复记忆合并失败，改为新增记忆"
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[{session_id}] 近重复检测不可用，按新记忆写入", exc_info=True
                )

        memory_id = await engine.add_memory(
            content=content,
            session_id=memory_scope,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
            atoms=atoms,
            source_messages=source_messages,
        )
        return memory_id, True, None

    async def _read_session_revision(self, session_id: str) -> int | None:
        """读取会话修订号；不可用（老版本/Mock）时返回 None，退化为不校验。"""
        manager = self.conversation_manager
        reader = getattr(manager, "get_session_revision", None) if manager else None
        if not callable(reader):
            return None
        try:
            value = await reader(session_id)
        except Exception:
            return None
        return value if isinstance(value, int) else None

    async def _revision_still_valid(
        self, session_id: str, expected_revision: int | None
    ) -> bool:
        """本任务是否仍然有效（会话未被回滚/清空）。"""
        if expected_revision is None:
            return True
        current = await self._read_session_revision(session_id)
        return current is not None and current == expected_revision

    async def _commit_session_metadata(
        self, session_id: str, key: str, value, expected_revision: int | None
    ) -> bool:
        """写入会话元数据；修订号可用时做原子校验（校验与写入不可分割）。

        Returns:
            bool: False 表示会话已被回滚/清空，写入被拒绝（调用方必须放弃）。
        """
        manager = self.conversation_manager
        if manager is None:
            return False
        conditional = getattr(manager, "update_session_metadata_if_revision", None)
        if expected_revision is not None and callable(conditional):
            return bool(await conditional(session_id, key, value, expected_revision))
        await manager.update_session_metadata(session_id, key, value)
        return True

    async def _commit_summary_cursor(
        self, session_id: str, end_index: int, expected_revision: int | None
    ) -> bool:
        """原子推进 ``last_summarized_index`` 并清除 ``pending_summary``。

        回滚会同时钳制这两个游标，因此这里必须与回滚互斥：修订号已变化时
        不得把游标写回旧位置（否则新消息会被跳过总结）。
        """
        committed = await self._commit_session_metadata(
            session_id, "last_summarized_index", end_index, expected_revision
        )
        if not committed:
            logger.warning(
                f"[{session_id}] 会话已回滚（修订号变化），"
                f"放弃把总结游标推进到 {end_index}"
            )
            return False
        await self._commit_session_metadata(
            session_id, "pending_summary", None, expected_revision
        )
        return True

    async def _compensate_rolled_back_memory(
        self, session_id: str, memory_id: int
    ) -> None:
        """回滚后补偿删除刚落库的记忆（关闭"校验通过 -> 写入完成"之间的窗口）。

        游标更新会被修订号校验正确拒绝，但内容此时已经 durable；仅靠"写入前校验"
        无法撤回，因此这里做补偿删除，避免已撤销内容留在长期记忆里。
        """
        if not self.memory_engine:
            return
        try:
            removed = await self.memory_engine.delete_memory(memory_id)
        except Exception as exc:
            logger.error(
                f"[{session_id}] 回滚补偿删除记忆 {memory_id} 失败: {exc}",
                exc_info=True,
            )
            return
        if not removed:
            logger.error(
                f"[{session_id}] 回滚补偿删除记忆 {memory_id} 未生效"
                f"（removed={removed}），已撤销内容可能仍可被检索"
            )
            return
        logger.warning(
            f"[{session_id}] 会话已回滚，已补偿删除刚落库的记忆 {memory_id}"
            f"，避免已撤销内容污染长期记忆"
        )

    async def _storage_task(
        self,
        session_id: str,
        history_messages: list[dict],
        persona_id: str,
        start_index: int,
        end_index: int,
        retry_count: int,
        memory_scope: str | None | object = _DEFAULT_MEMORY_SCOPE,
        expected_revision: int | None = None,
    ):
        """后台存储任务"""
        from ..utils import OperationContext

        if memory_scope is _DEFAULT_MEMORY_SCOPE:
            memory_scope = session_id

        # 会话修订号：WebUI 编辑/重试回滚或清空会话会推进修订号，使本任务
        # （持有回滚前的 history_messages 与总结范围）失效。
        # 调用方应在读取总结范围前捕获并传入；未传入时在此处补读。
        # 修订号不可用时（老版本会话管理器/测试替身）退化为不校验。
        if expected_revision is None:
            expected_revision = await self._read_session_revision(session_id)

        # 记忆 id 在 try 外初始化：取消可能发生在赋值之前，异常分支需要它
        memory_id: int | None = None
        memory_created = False
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

                if not await self._revision_still_valid(session_id, expected_revision):
                    logger.info(
                        f"[{session_id}] 会话已回滚（修订号变化），跳过过期总结 "
                        f"范围=[{start_index}:{end_index}]"
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
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                        expected_revision,
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
                        session_id=memory_scope,
                        persona_id=persona_id,
                    )

                    # 补充 source_window 元数据，记录本次总结的消息范围
                    metadata["source_window"] = {
                        "session_id": session_id,
                        "start_index": start_index,
                        "end_index": end_index,
                        "message_count": end_index - start_index,
                    }
                    metadata["source_session_id"] = session_id

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
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                        expected_revision,
                    )
                    return

                # 回滚会推进会话修订号：等待 LLM 期间若发生编辑/重试回滚，
                # 本任务的消息范围已失效，不得把被撤销内容写入长期记忆
                if not await self._revision_still_valid(session_id, expected_revision):
                    logger.info(
                        f"[{session_id}] 会话已回滚（修订号变化），放弃写入过期总结 "
                        f"范围=[{start_index}:{end_index}]"
                    )
                    return

                # 正常流程：写入记忆引擎；近重复检测命中时合并到已有记忆
                memory_id: int | None = None
                if self.memory_engine:
                    source_threshold = float(
                        self.config_manager.get(
                            "reflection_engine.source_retention_importance_threshold",
                            0.8,
                        )
                    )
                    source_messages = (
                        serialize_source_messages(history_messages)
                        if importance >= source_threshold
                        else None
                    )
                    (
                        memory_id,
                        memory_created,
                        dedup_similarity,
                    ) = await self._persist_summary_memory(
                        session_id=session_id,
                        memory_scope=memory_scope,
                        persona_id=persona_id,
                        content=content,
                        metadata=metadata,
                        importance=importance,
                        atoms=atoms,
                        source_messages=source_messages,
                    )
                    if memory_id is not None and not memory_created:
                        logger.info(
                            f"[{session_id}] 检测到近重复记忆，已合并到 #{memory_id}"
                            f"（相似度={dedup_similarity:.3f}），未新增记忆"
                        )
                    else:
                        logger.info(
                            f"[{session_id}] 成功存储对话记忆（{len(history_messages)}条消息，重要性={importance:.2f}）"
                        )

                # 成功：更新已总结的位置，清除待处理记录（原子校验修订号，
                # 避免把游标写回回滚前的旧位置）
                if self.conversation_manager:
                    try:
                        committed = await self._commit_summary_cursor(
                            session_id, end_index, expected_revision
                        )
                        if committed:
                            logger.info(
                                f"[{session_id}] 更新滑动窗口位置: last_summarized_index = {end_index}"
                            )
                        elif memory_id is not None and memory_created:
                            # 修订号校验发生在写入之前：等待期间若发生回滚，
                            # 刚落库的记忆属于已撤销内容，必须补偿删除
                            await self._compensate_rolled_back_memory(
                                session_id, memory_id
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
                            await self._commit_summary_cursor(
                                session_id, end_index, expected_revision
                            )
                        except Exception:
                            logger.error(
                                f"[{session_id}] 重试元数据更新仍然失败，"
                                "可能出现重复总结。",
                                exc_info=True,
                            )

            except asyncio.CancelledError:
                # 回滚会 cancel 本任务；取消在 await 点生效并穿透 except Exception。
                # 若取消发生在 add_memory 之后，内容已 durable，必须补偿删除
                # （shield：保证补偿不被这次取消再次打断）
                if memory_id is not None and memory_created:
                    await asyncio.shield(
                        self._compensate_rolled_back_memory(session_id, memory_id)
                    )
                raise
            except Exception as e:
                logger.error(f"[{session_id}] 存储记忆失败: {e}", exc_info=True)
                await self._record_pending_summary(
                    session_id,
                    start_index,
                    end_index,
                    retry_count,
                    expected_revision,
                )

    async def _record_pending_summary(
        self,
        session_id: str,
        start_index: int,
        end_index: int,
        current_retry_count: int,
        expected_revision: int | None = None,
    ):
        """记录待处理的失败总结信息（回滚后不得复活旧范围）。"""
        if not self.conversation_manager:
            return

        new_retry_count = current_retry_count + 1
        pending_summary = {
            "start_index": start_index,
            "end_index": end_index,
            "retry_count": new_retry_count,
        }

        committed = await self._commit_session_metadata(
            session_id, "pending_summary", pending_summary, expected_revision
        )
        if not committed:
            # 会话已被回滚/清空：该范围的消息已不存在，记录待重试会造成
            # 下一次触发按旧范围总结已撤销内容
            logger.info(
                f"[{session_id}] 会话已回滚（修订号变化），跳过记录待重试总结 "
                f"范围=[{start_index}:{end_index}]"
            )
            return

        logger.warning(
            f"[{session_id}] 记录待重试总结: 范围=[{start_index}:{end_index}], "
            f"重试次数={new_retry_count}/3"
        )

    def _on_storage_task_done(self, task: asyncio.Task, session_id: str):
        """存储任务完成回调"""
        self._storage_tasks.discard(task)
        self._storage_sessions_inflight.discard(session_id)
        if self._storage_session_tasks.get(session_id) is task:
            self._storage_session_tasks.pop(session_id, None)

        if task.cancelled():
            logger.info(f"[{session_id}] 存储任务已取消")
            return

        exc = task.exception()
        if exc:
            logger.error(f"[{session_id}] 存储任务异常: {exc}", exc_info=exc)

    def set_shutting_down(self, value: bool):
        """设置关闭状态"""
        self._shutting_down = value
