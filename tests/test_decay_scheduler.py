"""Tests for decay scheduler state handling."""

import builtins
from unittest.mock import Mock

import pytest
from astrbot_plugin_livingmemory.core.schedulers.decay_scheduler import DecayScheduler


@pytest.mark.asyncio
async def test_decay_scheduler_state_falls_back_without_aiofiles(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def import_without_aiofiles(name, *args, **kwargs):
        if name == "aiofiles":
            raise ImportError("aiofiles intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_aiofiles)

    scheduler = DecayScheduler(
        memory_engine=Mock(),
        decay_rate=0.01,
        data_dir=str(tmp_path),
    )

    await scheduler._save_state({"last_decay_date": "2026-06-01"})

    assert scheduler._state_file.exists()
    assert await scheduler._load_state() == {"last_decay_date": "2026-06-01"}


@pytest.mark.asyncio
async def test_decay_failure_does_not_mark_today(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """衰减执行失败时不能写“今日已衰减”，否则当天不会再重试。"""
    from unittest.mock import AsyncMock

    engine = Mock()
    engine.config = {}
    engine.apply_daily_decay = AsyncMock(return_value=-1)
    scheduler = DecayScheduler(
        memory_engine=engine,
        decay_rate=0.01,
        data_dir=str(tmp_path),
    )
    marked = AsyncMock()
    monkeypatch.setattr(scheduler, "_set_last_decay_date", marked)

    result = await scheduler._execute_decay(1)

    assert result is False
    marked.assert_not_awaited()
