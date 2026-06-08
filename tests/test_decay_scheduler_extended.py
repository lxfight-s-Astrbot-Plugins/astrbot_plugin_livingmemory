"""Extended tests for DecayScheduler to improve coverage."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
    """测试衰减执行"""

    @pytest.mark.asyncio
    async def test_execute_decay_with_zero_rate(self, tmp_path, mock_memory_engine):
        """测试衰减率为0时跳过衰减"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.0,  # 衰减率为0
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(1)
        assert success is True
        mock_memory_engine.apply_daily_decay.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_decay_normal(
        self, tmp_path, mock_memory_engine, mock_db_migration
    ):
        """测试正常衰减执行"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            db_migration=mock_db_migration,
            backup_enabled=True,
        )

        success = await scheduler._execute_decay(1)

        assert success is True
        mock_memory_engine.apply_daily_decay.assert_called_once_with(0.01, 1)
        mock_memory_engine.cleanup_old_memories.assert_called_once()
        mock_memory_engine.maintain_storage.assert_called_once()
        mock_db_migration.create_backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_decay_multiple_days(self, tmp_path, mock_memory_engine):
        """测试补偿多天衰减"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(5)

        assert success is True
        mock_memory_engine.apply_daily_decay.assert_called_once_with(0.01, 5)

    @pytest.mark.asyncio
    async def test_execute_decay_with_auto_cleanup_disabled(
        self, tmp_path, mock_memory_engine
    ):
        """测试禁用自动清理时不执行清理"""
        mock_memory_engine.config["auto_cleanup_enabled"] = False

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(1)

        assert success is True
        mock_memory_engine.cleanup_old_memories.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_decay_with_backup_disabled(
        self, tmp_path, mock_memory_engine, mock_db_migration
    ):
        """测试禁用备份时不执行备份"""
        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
            db_migration=mock_db_migration,
            backup_enabled=False,  # 禁用备份
        )

        success = await scheduler._execute_decay(1)

        assert success is True
        mock_db_migration.create_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_decay_handles_cleanup_error(
        self, tmp_path, mock_memory_engine
    ):
        """测试清理失败时仍然继续执行"""
        mock_memory_engine.cleanup_old_memories = AsyncMock(
            side_effect=Exception("Cleanup failed")
        )

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(1)

        # 清理失败不影响整体成功
        assert success is True
        mock_memory_engine.apply_daily_decay.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_decay_handles_maintenance_error(
        self, tmp_path, mock_memory_engine
    ):
        """测试存储维护失败时记录警告"""
        mock_memory_engine.maintain_storage = AsyncMock(
            side_effect=Exception("Maintenance failed")
        )

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(1)

        # 维护失败不影响整体成功
        assert success is True

    @pytest.mark.asyncio
    async def test_execute_decay_handles_decay_error(
        self, tmp_path, mock_memory_engine
    ):
        """测试衰减本身失败时返回False"""
        mock_memory_engine.apply_daily_decay = AsyncMock(
            side_effect=Exception("Decay failed")
        )

        scheduler = DecayScheduler(
            memory_engine=mock_memory_engine,
            decay_rate=0.01,
            data_dir=str(tmp_path),
        )

        success = await scheduler._execute_decay(1)

        assert success is False


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

        # 应该接近22小时（允许±1小时的误差）
        assert 78000 < seconds < 87000

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
