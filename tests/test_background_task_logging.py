"""Background failures must be surfaced instead of silently swallowed."""

import asyncio
from unittest.mock import AsyncMock, Mock

import astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection as reflection_module
import astrbot_plugin_livingmemory.main as main_module
import pytest
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
    MemoryReflection,
)

from .test_main_plugin_lifecycle import _make_plugin


@pytest.mark.asyncio
async def test_plugin_background_task_is_discarded_without_logging_cancel(
    monkeypatch,
):
    """取消属于正常关闭路径，不应被当成失败记录。"""
    plugin = _make_plugin()
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    async def _idle():
        await asyncio.Event().wait()

    task = plugin._create_tracked_task(_idle())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert task not in plugin._background_tasks
    logger_mock.error.assert_not_called()


@pytest.mark.asyncio
async def test_plugin_background_task_failure_is_logged(monkeypatch):
    """断言点：done 回调必须读取 task.exception() 并记录。"""
    plugin = _make_plugin()
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    async def _boom():
        raise RuntimeError("plugin background boom")

    plugin._create_tracked_task(_boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    logger_mock.error.assert_called_once()
    arguments, keyword_arguments = logger_mock.error.call_args
    assert "plugin background boom" in str(arguments)
    assert keyword_arguments.get("exc_info") is not None


@pytest.mark.asyncio
async def test_consolidation_task_failure_is_logged(monkeypatch):
    """断言点：_schedule_consolidation 的 done 回调必须记录任务异常。"""
    handler = MemoryReflection.__new__(MemoryReflection)
    handler._storage_tasks = set()
    handler._shutting_down = False
    handler.consolidation_manager = Mock()
    handler.consolidation_manager.maybe_run = AsyncMock(
        side_effect=RuntimeError("consolidation boom")
    )
    logger_mock = Mock()
    monkeypatch.setattr(reflection_module, "logger", logger_mock)

    handler._schedule_consolidation()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert handler._storage_tasks == set()
    logger_mock.error.assert_called_once()
    arguments, keyword_arguments = logger_mock.error.call_args
    assert "consolidation boom" in str(arguments)
    assert keyword_arguments.get("exc_info") is not None
