"""
记忆重要性衰减调度器
每日自动对记忆重要性进行衰减处理
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ..managers.memory_engine import MemoryEngine


class DecayScheduler:
    """
    记忆重要性衰减调度器

    功能：
    1. 每日凌晨自动执行衰减
    2. 启动时检查并补偿错过的衰减
    3. 防止同一天重复执行
    """

    def __init__(
        self,
        memory_engine: "MemoryEngine",
        decay_rate: float,
        data_dir: str,
        check_hour: int = 0,
        check_minute: int = 5,
    ):
        """
        初始化衰减调度器

        Args:
            memory_engine: 记忆引擎实例
            decay_rate: 每日衰减率 (0-1)
            data_dir: 数据目录，用于存储状态文件
            check_hour: 每日执行时间（小时）
            check_minute: 每日执行时间（分钟）
        """
        self.memory_engine = memory_engine
        self.decay_rate = decay_rate
        self.data_dir = Path(data_dir)
        self.check_hour = check_hour
        self.check_minute = check_minute

        self._state_file = self.data_dir / "decay_state.json"
        self._task: asyncio.Task | None = None
        self._running = False

    def _load_state(self) -> dict:
        """加载状态文件"""
        if not self._state_file.exists():
            return {}
        try:
            with open(self._state_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[衰减调度] 加载状态文件失败: {e}")
            return {}

    def _save_state(self, state: dict) -> None:
        """保存状态文件"""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[衰减调度] 保存状态文件失败: {e}")

    def _get_last_decay_date(self) -> str | None:
        """获取上次衰减日期 (格式: YYYY-MM-DD)"""
        state = self._load_state()
        return state.get("last_decay_date")

    def _set_last_decay_date(self, date_str: str) -> None:
        """设置上次衰减日期"""
        state = self._load_state()
        state["last_decay_date"] = date_str
        state["last_decay_timestamp"] = time.time()
        self._save_state(state)

    def _get_today_str(self) -> str:
        """获取今天日期字符串"""
        return datetime.now().strftime("%Y-%m-%d")

    def _calculate_missed_days(self) -> int:
        """计算错过的衰减天数"""
        last_date_str = self._get_last_decay_date()
        if not last_date_str:
            return 0

        try:
            last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            today = datetime.now().date()
            delta = (today - last_date).days
            return max(0, delta - 1)
        except ValueError:
            return 0

    async def _execute_decay(self, days: int = 1) -> bool:
        """
        执行衰减操作

        Args:
            days: 衰减天数（用于补偿错过的天数）

        Returns:
            是否执行成功
        """
        if self.decay_rate <= 0:
            logger.info("[衰减调度] 衰减率为0，跳过执行")
            return True

        try:
            affected = await self.memory_engine.apply_daily_decay(self.decay_rate, days)
            logger.info(
                f"[衰减调度] 衰减完成，影响 {affected} 条记忆，衰减天数: {days}"
            )
            self._set_last_decay_date(self._get_today_str())
            return True
        except Exception as e:
            logger.error(f"[衰减调度] 执行衰减失败: {e}", exc_info=True)
            return False

    async def _check_and_execute(self) -> None:
        """检查并执行衰减（启动时调用）"""
        today_str = self._get_today_str()
        last_date_str = self._get_last_decay_date()

        if last_date_str == today_str:
            logger.debug("[衰减调度] 今日已执行过衰减，跳过")
            return

        missed_days = self._calculate_missed_days()
        total_days = missed_days + 1

        if missed_days > 0:
            logger.info(f"[衰减调度] 检测到错过 {missed_days} 天衰减，执行补偿")

        await self._execute_decay(total_days)

    def _seconds_until_next_run(self) -> float:
        """计算距离下次执行的秒数"""
        now = datetime.now()
        target = now.replace(
            hour=self.check_hour,
            minute=self.check_minute,
            second=0,
            microsecond=0,
        )

        if now >= target:
            target += timedelta(days=1)

        return (target - now).total_seconds()

    async def _scheduler_loop(self) -> None:
        """调度器主循环"""
        while self._running:
            try:
                wait_seconds = self._seconds_until_next_run()
                logger.debug(f"[衰减调度] 下次执行在 {wait_seconds / 3600:.1f} 小时后")

                await asyncio.sleep(wait_seconds)

                if not self._running:
                    break

                await self._execute_decay(1)

            except asyncio.CancelledError:
                logger.info("[衰减调度] 调度器被取消")
                break
            except Exception as e:
                logger.error(f"[衰减调度] 循环异常: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("[衰减调度] 调度器已在运行")
            return

        self._running = True

        await self._check_and_execute()

        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(
            f"[衰减调度] 调度器已启动 (衰减率: {self.decay_rate}, "
            f"执行时间: {self.check_hour:02d}:{self.check_minute:02d})"
        )

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._task = None
        logger.info("[衰减调度] 调度器已停止")
