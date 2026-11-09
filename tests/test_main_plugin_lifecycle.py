"""Entry-point lifecycle tests for the LivingMemory plugin (``main.py``).

These tests drive ``LivingMemoryPlugin`` through a lightweight host stub:
the plugin object is created with ``__new__`` and only the collaborators the
method under test touches are injected, so lifecycle ordering and delegation
can be asserted without booting the AstrBot runtime.
"""

import asyncio
import contextlib
import sys
import types
from unittest.mock import AsyncMock, Mock

import astrbot_plugin_livingmemory.main as main_module
import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.main import LivingMemoryPlugin


def _make_initializer(*, initialized: bool = True, components: bool = True) -> Mock:
    """Build the PluginInitializer stub used by every lifecycle test."""
    initializer = Mock()
    initializer.is_initialized = initialized
    initializer.is_failed = False
    initializer.error_message = None
    initializer._provider_check_attempts = 0
    initializer.ensure_initialized = AsyncMock(return_value=initialized)
    initializer.memory_engine = Mock() if components else None
    initializer.memory_processor = Mock() if components else None
    initializer.conversation_manager = Mock() if components else None
    initializer.consolidation_manager = Mock() if components else None
    initializer.index_validator = Mock() if components else None
    initializer.stop_background_tasks = AsyncMock()
    initializer.stop_scheduler = AsyncMock()
    initializer.shutdown_index_persisters = AsyncMock()
    initializer.db = None
    return initializer


def _make_plugin(
    *,
    initialized: bool = True,
    components: bool = True,
    config: dict | None = None,
    event_handler: Mock | None = None,
    command_handler: Mock | None = None,
) -> LivingMemoryPlugin:
    """Create a plugin instance with the runtime attributes lifecycle code uses."""
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(config or {})
    plugin.initializer = _make_initializer(
        initialized=initialized, components=components
    )
    plugin.event_handler = event_handler
    plugin.command_handler = command_handler
    plugin._background_tasks = set()
    plugin._component_init_lock = asyncio.Lock()
    plugin._llm_tools_registered = False
    plugin._terminating = False
    plugin.page_api = Mock()
    plugin._backup_manager = Mock()
    plugin._external_api_server = None
    return plugin


def _make_event() -> Mock:
    """Minimal message event double accepted by adapter_access and plain_result."""
    event = Mock()
    event.unified_msg_origin = "test:private:session-1"
    event.get_platform_id = Mock(return_value="test")
    event.plain_result = Mock(side_effect=lambda message: ("plain", message))
    return event


def _fake_t(key: str, **kwargs) -> str:
    """Deterministic i18n double: expose the key and interpolation arguments."""
    return f"{key}|{sorted(kwargs.items())}"


# ==================== __init__ / 后台初始化 ====================


def test_plugin_init_wires_runtime_dependencies(tmp_path, monkeypatch):
    """Constructor creates managers, registers page routes, and starts init task."""
    data_dir = tmp_path / "plugin_data"
    star_tools = Mock()
    star_tools.get_data_dir = Mock(return_value=str(data_dir))
    backup_manager = Mock()
    initializer = Mock()
    page_api = Mock()
    set_active_plugin = Mock()
    i18n_init = Mock()
    scheduled = []

    def _track_task(self, coro):
        scheduled.append(coro)
        coro.close()
        return Mock()

    monkeypatch.setattr(main_module, "StarTools", star_tools)
    monkeypatch.setattr(main_module, "BackupManager", Mock(return_value=backup_manager))
    monkeypatch.setattr(
        main_module, "PluginInitializer", Mock(return_value=initializer)
    )
    monkeypatch.setattr(main_module, "PluginPageApi", Mock(return_value=page_api))
    monkeypatch.setattr(main_module, "set_active_plugin", set_active_plugin)
    monkeypatch.setattr(main_module, "i18n_init", i18n_init)
    monkeypatch.setattr(LivingMemoryPlugin, "_create_tracked_task", _track_task)

    context = Mock()
    plugin = LivingMemoryPlugin(context, {"bot_language": "en"})

    star_tools.get_data_dir.assert_called_once_with("astrbot_plugin_livingmemory")
    main_module.BackupManager.assert_called_once_with(str(data_dir))
    main_module.PluginInitializer.assert_called_once_with(
        context, plugin.config_manager, str(data_dir)
    )
    page_api.register_routes.assert_called_once_with()
    set_active_plugin.assert_called_once_with(plugin)
    i18n_init.assert_called_once_with("en")
    assert plugin.event_handler is None
    assert plugin.command_handler is None
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_initialize_plugin_backs_up_prunes_and_builds_components():
    plugin = _make_plugin()
    plugin._backup_manager = Mock()
    plugin._backup_manager.backup_if_needed_async = AsyncMock()
    plugin._backup_manager.prune_async = AsyncMock()
    plugin.initializer.initialize = AsyncMock(return_value=True)
    plugin._ensure_runtime_components = AsyncMock(return_value=True)

    await plugin._initialize_plugin()

    plugin._backup_manager.backup_if_needed_async.assert_awaited_once()
    plugin._backup_manager.prune_async.assert_awaited_once_with(keep_days=7)
    plugin.initializer.initialize.assert_awaited_once()
    plugin._ensure_runtime_components.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_plugin_continues_when_backup_prune_fails(monkeypatch):
    plugin = _make_plugin()
    plugin._backup_manager = Mock()
    plugin._backup_manager.backup_if_needed_async = AsyncMock()
    plugin._backup_manager.prune_async = AsyncMock(
        side_effect=RuntimeError("prune failed")
    )
    plugin.initializer.initialize = AsyncMock(return_value=False)
    plugin._ensure_runtime_components = AsyncMock(return_value=True)
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    await plugin._initialize_plugin()

    plugin.initializer.initialize.assert_awaited_once()
    # 初始化返回 False 时不构建组件
    plugin._ensure_runtime_components.assert_not_awaited()
    assert "prune failed" in str(logger_mock.warning.call_args)


@pytest.mark.asyncio
async def test_initialize_plugin_reports_backup_failure_and_stops(monkeypatch):
    plugin = _make_plugin()
    plugin._backup_manager = Mock()
    plugin._backup_manager.backup_if_needed_async = AsyncMock(
        side_effect=RuntimeError("backup failed")
    )
    plugin._backup_manager.prune_async = AsyncMock()
    plugin.initializer.initialize = AsyncMock(return_value=True)
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    await plugin._initialize_plugin()

    plugin.initializer.initialize.assert_not_awaited()
    logger_mock.error.assert_called_once()
    assert "backup failed" in str(logger_mock.error.call_args)


# ==================== 运行期组件 ====================


@pytest.mark.asyncio
async def test_ensure_runtime_components_requires_initialized_and_not_terminating():
    plugin = _make_plugin(initialized=False)
    assert await plugin._ensure_runtime_components() is False
    assert plugin.event_handler is None

    plugin = _make_plugin()
    plugin._terminating = True
    assert await plugin._ensure_runtime_components() is False


@pytest.mark.asyncio
async def test_ensure_runtime_components_reports_missing_core_components(monkeypatch):
    plugin = _make_plugin(components=False)
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    assert await plugin._ensure_runtime_components() is False
    logger_mock.error.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_runtime_components_is_idempotent(monkeypatch):
    plugin = _make_plugin()
    event_handler = Mock()
    command_handler = Mock()
    event_handler_ctor = Mock(return_value=event_handler)
    command_handler_ctor = Mock(return_value=command_handler)
    monkeypatch.setattr(main_module, "EventHandler", event_handler_ctor)
    monkeypatch.setattr(main_module, "CommandHandler", command_handler_ctor)

    assert await plugin._ensure_runtime_components() is True
    assert await plugin._ensure_runtime_components() is True

    event_handler_ctor.assert_called_once()
    command_handler_ctor.assert_called_once()
    # _register_agent_tools_if_needed 自身幂等：两次组件构建只注册一次工具
    plugin.context.add_llm_tools.assert_called_once()
    assert plugin.event_handler is event_handler
    assert plugin.command_handler is command_handler


# ==================== Agent 工具注册 ====================


TOOL_REGISTRATION_CASES = [
    ({}, ["recall_long_term_memory"]),
    (
        {"enable_recall_tool": True, "enable_agentic_recall_beta": True},
        ["recall_long_term_memory", "read_memory_evidence"],
    ),
    (
        {"enable_recall_tool": True, "enable_memorize_tool": True},
        ["recall_long_term_memory", "memorize_long_term_memory"],
    ),
    (
        {"enable_recall_tool": True, "enable_forget_tool": True},
        ["recall_long_term_memory", "forget_long_term_memory"],
    ),
    (
        {
            "enable_recall_tool": True,
            "enable_agentic_recall_beta": True,
            "enable_memorize_tool": True,
            "enable_forget_tool": True,
        },
        [
            "recall_long_term_memory",
            "read_memory_evidence",
            "memorize_long_term_memory",
            "forget_long_term_memory",
        ],
    ),
    (
        {
            "enable_recall_tool": False,
            "enable_agentic_recall_beta": True,
            "enable_memorize_tool": True,
        },
        ["memorize_long_term_memory"],
    ),
]


@pytest.mark.parametrize("agent_tools, expected_names", TOOL_REGISTRATION_CASES)
def test_register_agent_tools_honors_switch_combinations(agent_tools, expected_names):
    plugin = _make_plugin(config={"agent_tools": agent_tools})

    plugin._register_agent_tools_if_needed()

    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == expected_names
    assert plugin._llm_tools_registered is True

    plugin._register_agent_tools_if_needed()
    plugin.context.add_llm_tools.assert_called_once()


def test_register_agent_tools_waits_for_core_components():
    plugin = _make_plugin()
    plugin.initializer.memory_engine = None

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is False


# ==================== 命令入口 ====================


class _CommandRecorder:
    """Async-generator stand-in for a ``CommandHandler.handle_*`` method."""

    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._iterate()

    async def _iterate(self):
        try:
            yield "result-1"
            yield "result-2"
        finally:
            self.closed = True


COMMAND_DELEGATION_CASES = [
    # (plugin method, handler name, method args, handler args, handler kwargs, write)
    ("status", "handle_status", (), (), {}, False),
    ("search", "handle_search", ("query", 7), ("query", 7), {}, False),
    ("forget", "handle_forget", (42,), (42,), {}, False),
    ("rebuild_index", "handle_rebuild_index", (), (), {}, False),
    ("rebuild_graph", "handle_rebuild_graph", (), (), {}, False),
    (
        "migrate_scope",
        "handle_migrate_scope",
        ("target", "action", "source"),
        ("target", "action", "source"),
        {},
        False,
    ),
    ("webui", "handle_webui", (), (), {}, False),
    ("summarize", "handle_summarize", (5,), (5,), {}, True),
    ("reset", "handle_reset", (), (), {}, False),
    ("cleanup", "handle_cleanup", (), (), {"dry_run": True}, False),
    ("cleanup", "handle_cleanup", ("exec",), (), {"dry_run": False}, False),
    ("help", "handle_help", (), (), {}, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, handler_name, method_args, handler_args, handler_kwargs, write",
    COMMAND_DELEGATION_CASES,
)
async def test_commands_delegate_through_run_command(
    monkeypatch,
    method_name,
    handler_name,
    method_args,
    handler_args,
    handler_kwargs,
    write,
):
    plugin = _make_plugin(command_handler=Mock())
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    recorder = _CommandRecorder()
    setattr(plugin.command_handler, handler_name, recorder)

    adapter_flags = []

    @contextlib.contextmanager
    def _adapter_access(config, event, *, write=False):
        adapter_flags.append(write)
        yield

    monkeypatch.setattr(main_module, "adapter_access", _adapter_access)
    event = _make_event()

    messages = [
        message async for message in getattr(plugin, method_name)(event, *method_args)
    ]

    assert messages == ["result-1", "result-2"]
    assert len(recorder.calls) == 1
    call_args, call_kwargs = recorder.calls[0]
    assert call_args == (event, *handler_args)
    assert call_kwargs == handler_kwargs
    assert recorder.closed is True
    assert adapter_flags and all(flag is write for flag in adapter_flags)


@pytest.mark.asyncio
async def test_run_command_yields_status_message_when_plugin_not_ready():
    plugin = _make_plugin(command_handler=Mock())
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "还没就绪"))
    event = _make_event()

    messages = [message async for message in plugin.status(event)]

    assert messages == [("plain", "还没就绪")]
    plugin.command_handler.handle_status.assert_not_called()


@pytest.mark.asyncio
async def test_run_command_yields_hint_when_command_handler_missing(monkeypatch):
    monkeypatch.setattr(main_module, "t", _fake_t)
    plugin = _make_plugin(command_handler=None)
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    event = _make_event()

    messages = [message async for message in plugin.status(event)]

    assert messages == [("plain", "command.not_ready|[]")]


# ==================== 生命周期钩子 ====================


@pytest.mark.asyncio
async def test_recall_hook_requires_ready_plugin_and_event_handler():
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_memory_recall = AsyncMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "not ready"))
    event, request = Mock(), Mock()

    await plugin.handle_memory_recall(event, request)

    plugin._ensure_plugin_ready.assert_awaited_once_with(wait=False)
    plugin.event_handler.handle_memory_recall.assert_not_called()

    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.event_handler = None
    await plugin.handle_memory_recall(event, request)

    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.event_handler = Mock()
    plugin.event_handler.handle_memory_recall = AsyncMock()
    await plugin.handle_memory_recall(event, request)
    plugin.event_handler.handle_memory_recall.assert_awaited_once_with(event, request)


@pytest.mark.asyncio
async def test_reflection_hook_requires_ready_plugin_and_event_handler():
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_memory_reflection = AsyncMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "not ready"))
    event, response = Mock(), Mock()

    await plugin.handle_memory_reflection(event, response)

    plugin.event_handler.handle_memory_reflection.assert_not_called()

    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.event_handler = None
    await plugin.handle_memory_reflection(event, response)

    plugin.event_handler = Mock()
    plugin.event_handler.handle_memory_reflection = AsyncMock()
    await plugin.handle_memory_reflection(event, response)
    plugin.event_handler.handle_memory_reflection.assert_awaited_once_with(
        event, response
    )


@pytest.mark.asyncio
async def test_session_reset_hook_only_runs_for_reset_flags():
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_session_reset = AsyncMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plain_event = Mock()
    plain_event.get_extra = Mock(return_value=False)

    await plugin.handle_session_reset(plain_event)
    plugin.event_handler.handle_session_reset.assert_not_called()

    reset_event = Mock()
    reset_event.get_extra = Mock(
        side_effect=lambda key, default=None: key == "_clean_ltm_session"
    )
    await plugin.handle_session_reset(reset_event)
    plugin.event_handler.handle_session_reset.assert_awaited_once_with(reset_event)


@pytest.mark.asyncio
async def test_session_reset_hook_requires_ready_plugin():
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_session_reset = AsyncMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "not ready"))
    event = Mock()
    event.get_extra = Mock(return_value=True)

    await plugin.handle_session_reset(event)

    plugin.event_handler.handle_session_reset.assert_not_called()


@pytest.mark.asyncio
async def test_passive_filter_hook_is_a_noop():
    plugin = _make_plugin()

    assert await plugin.handle_all_group_messages(Mock()) is None


# ==================== 被动群聊捕获 ====================


@pytest.mark.asyncio
async def test_schedule_passive_group_capture_respects_lifecycle():
    plugin = _make_plugin()
    plugin._create_tracked_task = Mock(side_effect=lambda coro: coro.close())
    event = Mock()

    plugin._terminating = True
    plugin._schedule_passive_group_capture(event)
    plugin._create_tracked_task.assert_not_called()

    plugin._terminating = False
    plugin.initializer.is_initialized = False
    plugin._schedule_passive_group_capture(event)
    plugin._create_tracked_task.assert_not_called()

    plugin.initializer.is_initialized = True
    plugin._schedule_passive_group_capture(event)
    plugin._create_tracked_task.assert_called_once()


@pytest.mark.asyncio
async def test_passive_group_capture_skips_disabled_session(monkeypatch):
    plugin = _make_plugin()
    monkeypatch.setattr(
        main_module, "is_session_enabled", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=True)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=True)

    await plugin._run_passive_group_capture(Mock())

    plugin._ensure_runtime_components.assert_not_awaited()


@pytest.mark.asyncio
async def test_passive_group_capture_skips_session_disabled_by_plugin(monkeypatch):
    plugin = _make_plugin()
    monkeypatch.setattr(main_module, "is_session_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=False)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=True)

    await plugin._run_passive_group_capture(Mock())

    plugin._ensure_runtime_components.assert_not_awaited()


@pytest.mark.asyncio
async def test_passive_group_capture_skips_when_components_not_ready(monkeypatch):
    plugin = _make_plugin()
    monkeypatch.setattr(main_module, "is_session_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=True)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=False)

    await plugin._run_passive_group_capture(Mock())

    plugin._ensure_runtime_components.assert_awaited_once()


@pytest.mark.asyncio
async def test_passive_group_capture_delegates_to_event_handler(monkeypatch):
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_all_group_messages = AsyncMock()
    monkeypatch.setattr(main_module, "is_session_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=True)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=True)
    event = Mock()

    await plugin._run_passive_group_capture(event)

    plugin.event_handler.handle_all_group_messages.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_passive_group_capture_logs_unexpected_failure(monkeypatch):
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_all_group_messages = AsyncMock(
        side_effect=RuntimeError("capture failed")
    )
    monkeypatch.setattr(main_module, "is_session_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=True)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=True)
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    await plugin._run_passive_group_capture(Mock())

    logger_mock.error.assert_called_once()
    assert "capture failed" in str(logger_mock.error.call_args)


@pytest.mark.asyncio
async def test_passive_group_capture_propagates_cancellation(monkeypatch):
    plugin = _make_plugin(event_handler=Mock())
    plugin.event_handler.handle_all_group_messages = AsyncMock(
        side_effect=asyncio.CancelledError()
    )
    monkeypatch.setattr(main_module, "is_session_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main_module, "is_plugin_enabled_for_session", AsyncMock(return_value=True)
    )
    plugin._ensure_runtime_components = AsyncMock(return_value=True)

    with pytest.raises(asyncio.CancelledError):
        await plugin._run_passive_group_capture(Mock())


# ==================== 就绪状态与消息 ====================


@pytest.mark.asyncio
async def test_ensure_plugin_ready_reports_each_initialization_state(monkeypatch):
    monkeypatch.setattr(main_module, "t", _fake_t)
    plugin = _make_plugin(initialized=False)
    plugin.initializer.ensure_initialized = AsyncMock(return_value=False)
    plugin.initializer._provider_check_attempts = 3

    ready, message = await plugin._ensure_plugin_ready()
    assert ready is False
    assert message.startswith("init.in_progress|")
    assert "('attempts', 3)" in message

    plugin.initializer.is_failed = True
    plugin.initializer.error_message = "provider down"
    ready, message = await plugin._ensure_plugin_ready()
    assert ready is False
    assert "init.failed|" in message
    assert "provider down" in message

    plugin.initializer.is_failed = False
    plugin.initializer.error_message = None
    ready, message = await plugin._ensure_plugin_ready(wait=False)
    assert ready is False
    assert message.startswith("init.in_progress|")

    plugin.initializer.is_initialized = True
    plugin.initializer.is_failed = False
    plugin.initializer.ensure_initialized = AsyncMock(return_value=True)
    plugin._ensure_runtime_components = AsyncMock(return_value=False)
    ready, message = await plugin._ensure_plugin_ready()
    assert ready is False
    assert message == "command.core_not_ready|[]"

    plugin._ensure_runtime_components = AsyncMock(return_value=True)
    assert await plugin._ensure_plugin_ready() == (True, "")


def test_initialization_status_message_falls_back_to_unknown_error(monkeypatch):
    monkeypatch.setattr(main_module, "t", _fake_t)
    plugin = _make_plugin(initialized=False)
    plugin.initializer.is_failed = True
    plugin.initializer.error_message = None

    message = plugin._get_initialization_status_message()

    assert message.startswith("init.failed|")
    assert "common.unknown_error" in message


# ==================== 后台任务跟踪（含静默异常收口） ====================


@pytest.mark.asyncio
async def test_create_tracked_task_tracks_and_discards_completion():
    plugin = _make_plugin()

    async def _quick():
        return "done"

    task = plugin._create_tracked_task(_quick())
    assert task in plugin._background_tasks
    assert await task == "done"
    await asyncio.sleep(0)
    assert task not in plugin._background_tasks


@pytest.mark.asyncio
async def test_create_tracked_task_logs_unexpected_failure(monkeypatch):
    """断言点：done 回调必须读取 task.exception() 并记录，删掉日志则失败。"""
    plugin = _make_plugin()
    logger_mock = Mock()
    monkeypatch.setattr(main_module, "logger", logger_mock)

    async def _boom():
        raise RuntimeError("background boom")

    plugin._create_tracked_task(_boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    logger_mock.error.assert_called_once()
    arguments, keyword_arguments = logger_mock.error.call_args
    assert "background boom" in str(arguments)
    assert keyword_arguments.get("exc_info") is not None


# ==================== terminate ====================


@pytest.mark.asyncio
async def test_terminate_cancels_and_awaits_tasks_before_closing_components(
    monkeypatch,
):
    plugin = _make_plugin()
    order: list[str] = []

    def _marker(name: str):
        async def _mark():
            order.append(name)

        return _mark

    plugin.initializer.stop_background_tasks = AsyncMock(
        side_effect=_marker("stop_background_tasks")
    )
    plugin.initializer.stop_scheduler = AsyncMock(side_effect=_marker("stop_scheduler"))
    plugin.initializer.shutdown_index_persisters = AsyncMock(
        side_effect=_marker("shutdown_index_persisters")
    )
    plugin.initializer.conversation_manager = Mock()
    plugin.initializer.conversation_manager.store = Mock()
    plugin.initializer.conversation_manager.store.close = AsyncMock(
        side_effect=_marker("store_close")
    )
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_engine.close = AsyncMock(
        side_effect=_marker("memory_engine_close")
    )
    plugin.initializer.db = Mock()
    plugin.initializer.db.close = AsyncMock(side_effect=_marker("db_close"))
    plugin.event_handler = Mock()
    plugin.event_handler.shutdown = AsyncMock(
        side_effect=_marker("event_handler_shutdown")
    )

    set_active_plugin = Mock()
    monkeypatch.setattr(main_module, "get_active_plugin", Mock(return_value=plugin))
    monkeypatch.setattr(main_module, "set_active_plugin", set_active_plugin)

    cancelled = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def _worker():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            # 断言点：只有 terminate 里的 `await asyncio.gather(...)` 会等这段
            # 清理跑完；删掉 gather 后 terminate 提前返回，本事件不会被置位。
            # 用正的延迟确保「未等清理」和「等完清理」在事件循环顺序上可区分。
            await asyncio.sleep(0.05)
            cleanup_finished.set()
            raise

    task = plugin._create_tracked_task(_worker())
    # 让后台任务进入挂起点；cancel() 无法让尚未启动的协程执行 finally/except 分支。
    await asyncio.sleep(0)
    try:
        # 断言点：删掉 terminate 里的 task.cancel() 后 gather 会一直等待，
        # 这里的 wait_for 超时导致测试失败。
        await asyncio.wait_for(plugin.terminate(), timeout=2)

        assert cancelled.is_set()
        # 断言点：删掉 terminate 里的 `await asyncio.gather(...)` 后 terminate
        # 会在后台任务的清理完成前返回，本断言失败（finally 只在断言后清理）。
        assert cleanup_finished.is_set()
        assert task.cancelled()

        assert order == [
            "stop_background_tasks",
            "event_handler_shutdown",
            "stop_scheduler",
            "store_close",
            "shutdown_index_persisters",
            "memory_engine_close",
            "db_close",
        ]
        assert plugin._background_tasks == set()
        assert plugin._terminating is True
        set_active_plugin.assert_called_once_with(None)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_terminate_handles_uninitialized_components(monkeypatch):
    plugin = _make_plugin(components=False)
    plugin.initializer.stop_background_tasks = AsyncMock()
    plugin.initializer.stop_scheduler = AsyncMock()
    plugin.initializer.shutdown_index_persisters = AsyncMock()
    set_active_plugin = Mock()
    monkeypatch.setattr(main_module, "get_active_plugin", Mock(return_value=object()))
    monkeypatch.setattr(main_module, "set_active_plugin", set_active_plugin)

    await plugin.terminate()

    plugin.initializer.stop_background_tasks.assert_awaited_once()
    plugin.initializer.stop_scheduler.assert_awaited_once()
    plugin.initializer.shutdown_index_persisters.assert_awaited_once()
    # 不是当前 actiive 插件时不重置全局引用
    set_active_plugin.assert_not_called()


# ==================== 宿主版本检测 ====================


def test_detect_astrbot_version_falls_back_to_module_version(monkeypatch):
    version_lookup = Mock(
        side_effect=main_module.importlib_metadata.PackageNotFoundError()
    )
    monkeypatch.setattr(main_module.importlib_metadata, "version", version_lookup)
    module = types.ModuleType("astrbot.core.config.default")
    module.VERSION = "4.25.0"
    monkeypatch.setitem(sys.modules, "astrbot.core.config.default", module)

    assert main_module._detect_astrbot_version() == "4.25.0"


def test_detect_astrbot_version_skips_broken_modules(monkeypatch):
    version_lookup = Mock(
        side_effect=main_module.importlib_metadata.PackageNotFoundError()
    )
    monkeypatch.setattr(main_module.importlib_metadata, "version", version_lookup)
    monkeypatch.setitem(sys.modules, "astrbot.core.config.default", None)
    module = types.ModuleType("astrbot.core.config")
    module.VERSION = "4.25.0"
    monkeypatch.setitem(sys.modules, "astrbot.core.config", module)

    assert main_module._detect_astrbot_version() == "4.25.0"


def test_detect_astrbot_version_returns_none_without_version(monkeypatch):
    version_lookup = Mock(
        side_effect=main_module.importlib_metadata.PackageNotFoundError()
    )
    monkeypatch.setattr(main_module.importlib_metadata, "version", version_lookup)
    for module_name in ("astrbot.core.config.default", "astrbot.core.config"):
        module = types.ModuleType(module_name)
        module.VERSION = None
        monkeypatch.setitem(sys.modules, module_name, module)

    assert main_module._detect_astrbot_version() is None
