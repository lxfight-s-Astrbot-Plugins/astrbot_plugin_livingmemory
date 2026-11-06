"""记忆反思的后台存储任务：LLM 总结、游标提交、回滚补偿与修订号校验。"""

import asyncio
from contextlib import nullcontext
from typing import Any

from astrbot.api import logger

from ..memory_source import serialize_source_messages

_DEFAULT_MEMORY_SCOPE = object()


class MemoryReflectionStorageMixin:
    """后台总结任务与提交补偿；宿主状态由 MemoryReflection 持有。"""

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
        lock = (
            self.consolidation_manager.write_lock
            if self.consolidation_manager is not None
            else nullcontext()
        )
        async with lock:
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

    async def _compensate_rolled_back_memory(
        self, session_id: str, memory_id: int
    ) -> None:
        """回滚后补偿删除刚落库的记忆（关闭"校验通过 -> 写入完成"之间的窗口）。

        游标更新会被修订号校验正确拒绝，但内容此时已经 durable；仅靠"写入前校验"
        无法撤回，因此这里做补偿删除，避免已撤销内容留在长期记忆里。
        """
        lock = (
            self.consolidation_manager.write_lock
            if self.consolidation_manager is not None
            else nullcontext()
        )
        async with lock:
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
    ) -> bool:
        """总结并提交一个窗口片段；返回是否成功推进总结游标。"""
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
                    return False

                if not await self._revision_still_valid(session_id, expected_revision):
                    logger.info(
                        f"[{session_id}] 会话已回滚（修订号变化），跳过过期总结 "
                        f"范围=[{start_index}:{end_index}]"
                    )
                    return False

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
                    return False

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
                    return False

                # 回滚会推进会话修订号：等待 LLM 期间若发生编辑/重试回滚，
                # 本任务的消息范围已失效，不得把被撤销内容写入长期记忆
                if not await self._revision_still_valid(session_id, expected_revision):
                    logger.info(
                        f"[{session_id}] 会话已回滚（修订号变化），放弃写入过期总结 "
                        f"范围=[{start_index}:{end_index}]"
                    )
                    return False

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

                committed = await self.conversation_manager.commit_summary_cursor(
                    session_id, history_messages[-1], expected_revision
                )
                if not committed and memory_id is not None and memory_created:
                    await self._compensate_rolled_back_memory(session_id, memory_id)
                return committed

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
                return False

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
