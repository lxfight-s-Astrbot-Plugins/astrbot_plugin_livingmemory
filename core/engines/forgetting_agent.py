# -*- coding: utf-8 -*-
"""
forgetting_agent.py - 遗忘代理
作为一个后台任务，定期清理陈旧的、不重要的记忆，模拟人类的遗忘曲线。
"""

import asyncio
from typing import Dict, Any, Optional

from astrbot.api import logger
from astrbot.api.star import Context
from ...storage.faiss_manager import FaissManager
from ..utils import get_now_datetime, safe_parse_metadata, validate_timestamp


class ForgettingAgent:
    """
    遗忘代理：作为一个后台任务，定期清理陈旧的、不重要的记忆，模拟人类的遗忘曲线。
    """

    def __init__(
        self, context: Context, config: Dict[str, Any], faiss_manager: FaissManager
    ):
        """
        初始化遗忘代理。

        Args:
            context (Context): AstrBot 的上下文对象。
            config (Dict[str, Any]): 插件配置中 'forgetting_agent' 部分的字典。
            faiss_manager (FaissManager): 数据库管理器实例。
        """
        self.context = context
        self.config = config
        self.faiss_manager = faiss_manager
        self._task: Optional[asyncio.Task] = None
        logger.info("ForgettingAgent 初始化成功。")

    async def start(self):
        """启动后台遗忘任务。"""
        if not self.config.get("enabled", True):
            logger.info("遗忘代理未启用，不启动后台任务。")
            return

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_periodically())
            logger.info("遗忘代理后台任务已启动。")

    async def stop(self):
        """停止后台遗忘任务。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("遗忘代理后台任务已成功取消。")
        self._task = None

    async def _run_periodically(self):
        """后台任务的循环体。"""
        interval_hours = self.config.get("check_interval_hours", 24)
        interval_seconds = interval_hours * 3600
        logger.info(f"遗忘代理将每 {interval_hours} 小时运行一次。")

        while True:
            try:
                await asyncio.sleep(interval_seconds)
                logger.info("开始执行每日记忆清理任务...")
                await self._prune_memories()
                logger.info("每日记忆清理任务执行完毕。")
            except asyncio.CancelledError:
                logger.info("遗忘代理任务被取消。")
                break
            except Exception as e:
                logger.error(f"遗忘代理后台任务发生错误: {e}", exc_info=True)
                # 即使出错，也等待下一个周期，避免快速失败刷屏
                await asyncio.sleep(60)

    async def _prune_memories(self):
        """执行一次完整的记忆衰减和修剪，使用分页处理避免内存过载。"""
        # 获取记忆总数
        total_memories = await self.faiss_manager.count_total_memories()
        if total_memories == 0:
            logger.info("数据库中没有记忆，无需清理。")
            return

        retention_days = self.config.get("retention_days", 90)
        decay_rate = self.config.get("importance_decay_rate", 0.005)
        current_time = get_now_datetime(self.context).timestamp()

        # 分页处理配置
        page_size = self.config.get("forgetting_batch_size", 1000)  # 每批处理数量

        logger.info(f"开始处理 {total_memories} 条记忆，每批 {page_size} 条")

        memories_to_update = []
        ids_to_delete = []
        total_processed = 0

        # 分页处理所有记忆
        for offset in range(0, total_memories, page_size):
            batch_memories = await self.faiss_manager.get_memories_paginated(
                page_size=page_size, offset=offset
            )

            if not batch_memories:
                break

            logger.debug(
                f"处理第 {offset // page_size + 1} 批，共 {len(batch_memories)} 条记忆"
            )

            batch_updates = []
            batch_deletes = []

            for mem in batch_memories:
                # 使用统一的元数据解析函数
                metadata = safe_parse_metadata(mem["metadata"])
                if not metadata:
                    logger.warning(f"无法解析记忆 {mem['id']} 的元数据，跳过处理")
                    continue

                # 1. 重要性衰减
                create_time = validate_timestamp(
                    metadata.get("create_time"), current_time
                )
                days_since_creation = (current_time - create_time) / (24 * 3600)

                # 线性衰减
                decayed_importance = metadata.get("importance", 0.5) - (
                    days_since_creation * decay_rate
                )
                metadata["importance"] = max(0, decayed_importance)  # 确保不为负

                mem["metadata"] = metadata  # 更新内存中的 metadata
                batch_updates.append(mem)

                # 2. 识别待删除项
                retention_seconds = retention_days * 24 * 3600
                is_old = (current_time - create_time) > retention_seconds
                # 从配置中读取重要性阈值
                importance_threshold = self.config.get("importance_threshold", 0.1)
                is_unimportant = metadata["importance"] < importance_threshold

                if is_old and is_unimportant:
                    batch_deletes.append(mem["id"])

            # 累积到全局列表
            memories_to_update.extend(batch_updates)
            ids_to_delete.extend(batch_deletes)
            total_processed += len(batch_memories)

            # 如果批次数据过多，执行中间提交
            if len(memories_to_update) >= page_size * 2:
                logger.debug(f"执行中间批次更新，更新 {len(memories_to_update)} 条记忆")
                await self.faiss_manager.update_memories_metadata(memories_to_update)
                memories_to_update.clear()

            logger.debug(f"已处理 {total_processed}/{total_memories} 条记忆")

        # 3. 执行最终数据库操作
        if memories_to_update:
            await self.faiss_manager.update_memories_metadata(memories_to_update)
            logger.info(f"更新了 {len(memories_to_update)} 条记忆的重要性得分。")

        if ids_to_delete:
            # 分批删除，避免一次删除太多
            delete_batch_size = 100
            for i in range(0, len(ids_to_delete), delete_batch_size):
                batch = ids_to_delete[i : i + delete_batch_size]
                await self.faiss_manager.delete_memories(batch)
                logger.debug(f"删除了 {len(batch)} 条记忆")

            logger.info(f"总共删除了 {len(ids_to_delete)} 条陈旧且不重要的记忆。")

        logger.info(f"记忆清理完成，处理了 {total_processed} 条记忆")
