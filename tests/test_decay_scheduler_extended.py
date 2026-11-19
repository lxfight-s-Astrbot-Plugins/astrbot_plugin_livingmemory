"""Extended tests for DecayScheduler to improve coverage."""

import asyncio
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.schedulers.decay_scheduler import DecayScheduler


@pytest.fixture
def mock_memory_engine():
    """创建mock的MemoryEngine"""
    engine = Mock()
    engine.apply_daily_decay = AsyncMock(return_value=10)
    engine.cleanup_old_memories = AsyncMock(return_value=5)
    engine.maintain_storage = AsyncMock(
        return_value={"success": True, "bytes_reclaimed": 1024 * 1024}
    )
    engine.config = {
        "auto_cleanup_enabled": True,
        "cleanup_days_threshold": 30,
        "cleanup_importance_threshold": 0.3,
    }
    return engine


@pytest.fixture
def mock_db_migration():
    """创建mock的DBMigration"""
    migration = Mock()
    migration.db_path = "/tmp/test.db"
    migration.create_backup = AsyncMock(return_value="/tmp/backup.db")
    return migration


class TestDecaySchedulerStateManagement:
    """测试状态文件管理"""

    @pytest.mark.asyncio
    async def test_load_state_when_file_not_exists(self, tmp_path, mock_memory_engine):
        """测试状态文件不存在时返回空字典"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        state = await scheduler._load_state()
        assert state == {}

    @pytest.mark.asyncio
    async def test_load_state_with_corrupted_json(
        self, tmp_path, mock_memory_engine
    ):
        """测试损坏的JSON文件返回空字典"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 写入损坏的JSON
        scheduler._state_file.write_text("not a valid json{", encoding="utf-8")

        state = await scheduler._load_state()
        assert state == {}

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, tmp_path, mock_memory_engine):
        """测试状态保存和加载"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        test_state = {"last_decay_date": "2026-06-08", "test_key": "test_value"}
        await scheduler._save_state(test_state)

        loaded = await scheduler._load_state()
        assert loaded == test_state

    @pytest.mark.asyncio
    async def test_set_and_get_last_decay_date(self, tmp_path, mock_memory_engine):
        """测试设置和获取最后衰减日期"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 初始为None
        date = await scheduler._get_last_decay_date()
        assert date is None

        # 设置日期
        await scheduler._set_last_decay_date("2026-06-07")

        # 验证可以读取
        date = await scheduler._get_last_decay_date()
        assert date == "2026-06-07"

        # 验证timestamp也被保存
        state = await scheduler._load_state()
        assert "last_decay_timestamp" in state


class TestDecaySchedulerMissedDaysCalculation:
    """测试错过天数计算"""

    @pytest.mark.asyncio
    async def test_calculate_missed_days_no_previous_run(
        self, tmp_path, mock_memory_engine
    ):
        """测试从未执行过时返回0"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        missed = await scheduler._calculate_missed_days()
        assert missed == 0

    @pytest.mark.asyncio
    async def test_calculate_missed_days_same_day(self, tmp_path, mock_memory_engine):
        """测试同一天不算错过"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        today = datetime.now().strftime("%Y-%m-%d")
        await scheduler._set_last_decay_date(today)

        missed = await scheduler._calculate_missed_days()
        assert missed == 0

    @pytest.mark.asyncio
    async def test_calculate_missed_days_multiple_days(
        self, tmp_path, mock_memory_engine
    ):
        """测试错过多天"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 设置为3天前
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        await scheduler._set_last_decay_date(three_days_ago)

        missed = await scheduler._calculate_missed_days()
        assert missed == 2  # 3天前到今天，错过了2天（不包括今天）

    @pytest.mark.asyncio
    async def test_calculate_missed_days_invalid_date_format(
        self, tmp_path, mock_memory_engine
    ):
        """测试无效日期格式返回0"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        await scheduler._save_state({"last_decay_date": "invalid-date"})

        missed = await scheduler._calculate_missed_days()
        assert missed == 0


class TestDecaySchedulerExecution:
    """衰减执行：用真实引擎验证记忆重要性确实下降。"""

    @staticmethod
    async def _engine_and_memory(tmp_path):
        from tests.real_engine_support import make_engine

        engine, db = await make_engine(
            tmp_path / "decay", config={"auto_cleanup_enabled": False}
        )
        memory_id = await engine.add_memory(
            "用户喜欢燕麦拿铁",
            session_id="livingmemory:global",
            persona_id=None,
            # 高于 protected_importance_threshold(0.6) 的记忆受保护，
            # 这里用 0.5 以便观察衰减效果
            importance=0.5,
        )
        return engine, db, memory_id

    @staticmethod
    async def _importance(engine, memory_id: int) -> float:
        record = await engine.get_memory_record(memory_id)
        metadata = json.loads(record["metadata"])
        return float(metadata["importance"])

    @pytest.mark.asyncio
    async def test_decay_reduces_importance(self, tmp_path):
        from tests.real_engine_support import close_engine

        engine, db, memory_id = await self._engine_and_memory(tmp_path)
        try:
            before = await self._importance(engine, memory_id)
            scheduler = DecayScheduler(
                memory_engine=engine, decay_rate=0.01, data_dir=str(tmp_path)
            )

            success = await scheduler._execute_decay(1)

            after = await self._importance(engine, memory_id)
        finally:
            await close_engine(engine, db)

        assert success is True
        assert after < before
        assert after == pytest.approx(before * 0.99, abs=1e-6)

    @pytest.mark.asyncio
    async def test_zero_rate_leaves_importance_untouched(self, tmp_path):
        from tests.real_engine_support import close_engine

        engine, db, memory_id = await self._engine_and_memory(tmp_path)
        try:
            before = await self._importance(engine, memory_id)
            scheduler = DecayScheduler(
                memory_engine=engine, decay_rate=0.0, data_dir=str(tmp_path)
            )

            success = await scheduler._execute_decay(1)

            after = await self._importance(engine, memory_id)
        finally:
            await close_engine(engine, db)

        assert success is True
        assert after == pytest.approx(before)

    @pytest.mark.asyncio
    async def test_missed_days_are_compensated(self, tmp_path):
        """补偿多天时按天数累计衰减，而不是只衰减一次。"""
        from tests.real_engine_support import close_engine

        engine, db, memory_id = await self._engine_and_memory(tmp_path)
        try:
            before = await self._importance(engine, memory_id)
            scheduler = DecayScheduler(
                memory_engine=engine, decay_rate=0.01, data_dir=str(tmp_path)
            )

            await scheduler._execute_decay(5)

            after = await self._importance(engine, memory_id)
        finally:
            await close_engine(engine, db)

        # 重要性按 6 位小数落库，比较时放宽到该精度
        assert after == pytest.approx(before * (0.99**5), abs=1e-4)
        assert after < before * 0.99  # 明显多于单日衰减

    @pytest.mark.asyncio
    async def test_decay_records_the_run_date(self, tmp_path):
        """执行后写入状态文件，避免同一天重复衰减。"""
        from tests.real_engine_support import close_engine

        engine, db, _memory_id = await self._engine_and_memory(tmp_path)
        try:
            scheduler = DecayScheduler(
                memory_engine=engine, decay_rate=0.01, data_dir=str(tmp_path)
            )

            await scheduler._execute_decay(1)

            state = await scheduler._load_state()
        finally:
            await close_engine(engine, db)

        assert state.get("last_decay_date") == scheduler._get_today_str()

    @pytest.mark.asyncio
    async def test_cleanup_runs_only_when_enabled(self, tmp_path):
        """启用自动清理时，旧记忆会被清理掉（默认归档）。"""
        from tests.real_engine_support import close_engine, make_engine

        engine, db = await make_engine(
            tmp_path / "cleanup",
            config={
                "auto_cleanup_enabled": True,
                "auto_archived_enabled": True,
                "cleanup_days_threshold": 1,
                "cleanup_importance_threshold": 1.0,
            },
        )
        try:
            old = await engine.add_memory(
                "很久以前的低重要性记忆",
                session_id="livingmemory:global",
                persona_id=None,
                importance=0.05,
                preserve_create_time=True,
                metadata={"create_time": time.time() - 10 * 86400},
            )
            scheduler = DecayScheduler(
                memory_engine=engine, decay_rate=0.01, data_dir=str(tmp_path)
            )

            await scheduler._execute_decay(1)

            record = await engine.get_memory_record(old)
            assert json.loads(record["metadata"])["status"] == "archived"
        finally:
            await close_engine(engine, db)

    @pytest.mark.asyncio
    async def test_decay_failure_is_reported(self, tmp_path):
        """衰减本身失败时返回 False，调度器不会误报成功。"""

        class _BrokenEngine:
            config = {"auto_cleanup_enabled": False}

            async def apply_daily_decay(self, rate, days):
                raise RuntimeError("Decay failed")

        scheduler = DecayScheduler(
            memory_engine=_BrokenEngine(), decay_rate=0.01, data_dir=str(tmp_path)
        )

        assert await scheduler._execute_decay(1) is False

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_fail_the_run(self, tmp_path):
        """清理失败不影响整次衰减，状态仍会推进。"""

        class _CleanupBrokenEngine:
            config = {
                "auto_cleanup_enabled": True,
                "cleanup_days_threshold": 30,
                "cleanup_importance_threshold": 0.3,
            }

            async def apply_daily_decay(self, rate, days):
                return 1

            async def cleanup_old_memories(self, **_kwargs):
                raise RuntimeError("Cleanup failed")

        scheduler = DecayScheduler(
            memory_engine=_CleanupBrokenEngine(),
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        assert await scheduler._execute_decay(1) is True
        state = await scheduler._load_state()
        assert state.get("last_decay_date") == scheduler._get_today_str()


class TestDecaySchedulerBackup:
    """测试备份功能"""

    @pytest.mark.asyncio
    async def test_run_backup_success(self, tmp_path, mock_memory_engine, mock_db_migration):
        """测试成功备份"""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            db_migration=mock_db_migration,
        )

        await scheduler._run_backup()

        mock_db_migration.create_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_backup_without_migration(self, tmp_path, mock_memory_engine):
        """测试没有migration时跳过备份"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            db_migration=None,  # 没有migration
        )

        # 不应该抛出异常
        await scheduler._run_backup()

    @pytest.mark.asyncio
    async def test_cleanup_old_backups(self, tmp_path, mock_memory_engine, mock_db_migration):
        """测试清理过期备份"""
        # 创建backup目录和测试文件
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # 创建一个旧备份文件（10天前）
        old_backup = backup_dir / "test_backup_old.db"
        old_backup.touch()
        old_time = datetime.now().timestamp() - 10 * 86400
        import os
        os.utime(old_backup, (old_time, old_time))

        # 创建一个新备份文件（1天前）
        new_backup = backup_dir / "test_backup_new.db"
        new_backup.touch()

        mock_db_migration.db_path = str(tmp_path / "test.db")

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            db_migration=mock_db_migration,
            backup_keep_days=7,
        )

        await scheduler._cleanup_old_backups()

        # 旧文件应该被删除，新文件应该保留
        assert not old_backup.exists()
        assert new_backup.exists()


class TestDecaySchedulerScheduling:
    """测试调度逻辑"""

    def test_seconds_until_next_run_future_today(self, tmp_path, mock_memory_engine):
        """测试计算到今天目标时间的秒数"""
        now = datetime.now()
        # 设置目标时间为当前时间+2小时
        target_hour = (now.hour + 2) % 24

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            check_hour=target_hour,
            check_minute=0,
        )

        seconds = scheduler._seconds_until_next_run()

        # 应该在1-3小时之间（考虑到边界情况）
        assert 3000 < seconds < 12000

    def test_seconds_until_next_run_past_today(self, tmp_path, mock_memory_engine):
        """测试目标时间已过时计算到明天的秒数"""
        now = datetime.now()
        # 设置目标时间为当前时间-2小时
        past_hour = (now.hour - 2) % 24

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            check_hour=past_hour,
            check_minute=0,
        )

        seconds = scheduler._seconds_until_next_run()

        # 应该接近22小时（允许更大的误差范围，20-24小时之间）
        assert 72000 < seconds < 87000  # 20-24小时

    @pytest.mark.asyncio
    async def test_check_and_execute_already_run_today(
        self, tmp_path, mock_memory_engine
    ):
        """测试今天已执行时跳过"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 设置为今天已执行
        today = datetime.now().strftime("%Y-%m-%d")
        await scheduler._set_last_decay_date(today)

        await scheduler._check_and_execute()

        # 不应该再次执行衰减
        mock_memory_engine.apply_daily_decay.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_execute_with_missed_days(
        self, tmp_path, mock_memory_engine
    ):
        """测试有错过天数时补偿执行"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 设置为3天前
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        await scheduler._set_last_decay_date(three_days_ago)

        await scheduler._check_and_execute()

        # 应该执行3天的衰减（错过2天 + 今天1天）
        mock_memory_engine.apply_daily_decay.assert_called_once_with(0.01, 3)

    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_path, mock_memory_engine):
        """测试启动和停止调度器"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        # 启动
        await scheduler.start()
        assert scheduler._running is True
        assert scheduler._task is not None

        # 再次启动应该警告
        await scheduler.start()  # 不应该崩溃

        # 停止
        await scheduler.stop()
        assert scheduler._running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_scheduler_loop_cancellation(self, tmp_path, mock_memory_engine):
        """测试调度循环可以被取消"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            check_hour=23,  # 设置为晚上，避免立即触发
            check_minute=59,
        )

        await scheduler.start()
        await asyncio.sleep(0.1)  # 让循环开始

        # 保存task引用，因为stop()会将其设为None
        task = scheduler._task
        assert task is not None

        await scheduler.stop()

        # 任务应该被取消
        assert task.done() or task.cancelled()
        assert scheduler._task is None


class TestDecaySchedulerGetTodayStr:
    """测试日期字符串生成"""

    def test_get_today_str_format(self, tmp_path, mock_memory_engine):
        """测试今天日期字符串格式正确"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        today_str = scheduler._get_today_str()

        # 验证格式
        assert len(today_str) == 10
        assert today_str.count("-") == 2

        # 验证可以解析
        datetime.strptime(today_str, "%Y-%m-%d")
