"""Regression checks for full group capture not waking ordinary messages."""

from unittest.mock import Mock
from unittest.mock import AsyncMock, patch

import pytest
from astrbot.api.platform import MessageType

from astrbot_plugin_livingmemory.main import PassiveGroupCaptureFilter


class AlwaysPassFilter:
    def filter(self, event, cfg):
        return True


def _simulate_waking_check(filters, event):
    """Model AstrBot's key WakingCheck behavior for adapter handlers."""
    cfg = {
        "platform_settings": {
            "enable_id_white_list": False,
            "id_whitelist": [],
        }
    }
    return _simulate_waking_check_with_cfg(filters, event, cfg)


def _simulate_waking_check_with_cfg(filters, event, cfg):
    """Model AstrBot's key WakingCheck behavior with a supplied config."""
    is_wake = False
    passed = True
    for filter_ in filters:
        if not filter_.filter(event, cfg):
            passed = False
            break
    if passed:
        is_wake = True
        event.is_wake = True
    return is_wake


def _make_plugin(initialized=True, runtime_ready=True):
    plugin = Mock()
    plugin.initializer = Mock(is_initialized=initialized)
    plugin.config_manager.get.return_value = True
    plugin._schedule_passive_group_capture = Mock()

    async def _ensure_runtime_components():
        return runtime_ready

    plugin._ensure_runtime_components = _ensure_runtime_components
    return plugin


def _make_event(message_type=MessageType.GROUP_MESSAGE):
    event = Mock()
    event.get_message_type.return_value = message_type
    event.get_platform_name.return_value = "aiocqhttp"
    event.get_group_id.return_value = "group-1"
    event.unified_msg_origin = "aiocqhttp:GroupMessage:group-1"
    event.is_wake = False
    return event


def test_old_always_pass_handler_wakes_ordinary_message():
    event = _make_event()

    assert _simulate_waking_check([AlwaysPassFilter()], event) is True
    assert event.is_wake is True


def test_passive_group_capture_filter_schedules_capture_without_waking():
    plugin = _make_plugin()
    event = _make_event()
    filter_ = PassiveGroupCaptureFilter(plugin)

    assert _simulate_waking_check([filter_], event) is False
    assert event.is_wake is False
    plugin._schedule_passive_group_capture.assert_called_once_with(event)


def test_passive_group_capture_filter_ignores_private_messages():
    plugin = _make_plugin()
    event = _make_event(MessageType.FRIEND_MESSAGE)
    filter_ = PassiveGroupCaptureFilter(plugin)

    assert _simulate_waking_check([filter_], event) is False
    plugin._schedule_passive_group_capture.assert_not_called()


def test_passive_group_capture_filter_ignores_uninitialized_plugin():
    plugin = _make_plugin(initialized=False)
    event = _make_event()
    filter_ = PassiveGroupCaptureFilter(plugin)

    assert _simulate_waking_check([filter_], event) is False
    plugin._schedule_passive_group_capture.assert_not_called()


def test_passive_group_capture_filter_ignores_disabled_capture_config():
    plugin = _make_plugin()
    plugin.config_manager.get.return_value = False
    event = _make_event()
    filter_ = PassiveGroupCaptureFilter(plugin)

    assert _simulate_waking_check([filter_], event) is False
    plugin._schedule_passive_group_capture.assert_not_called()


def test_passive_group_capture_filter_respects_global_whitelist():
    plugin = _make_plugin()
    event = _make_event()
    filter_ = PassiveGroupCaptureFilter(plugin)
    cfg = {
        "platform_settings": {
            "enable_id_white_list": True,
            "id_whitelist": ["other-group"],
        }
    }

    assert _simulate_waking_check_with_cfg([filter_], event, cfg) is False
    plugin._schedule_passive_group_capture.assert_not_called()


def test_passive_group_capture_filter_respects_admin_whitelist_bypass():
    plugin = _make_plugin()
    event = _make_event()
    event.role = "admin"
    filter_ = PassiveGroupCaptureFilter(plugin)
    cfg = {
        "platform_settings": {
            "enable_id_white_list": True,
            "id_whitelist": ["other-group"],
            "wl_ignore_admin_on_group": True,
        }
    }

    assert _simulate_waking_check_with_cfg([filter_], event, cfg) is False
    plugin._schedule_passive_group_capture.assert_called_once_with(event)


def test_passive_group_capture_filter_allows_whitelisted_group():
    plugin = _make_plugin()
    event = _make_event()
    filter_ = PassiveGroupCaptureFilter(plugin)
    cfg = {
        "platform_settings": {
            "enable_id_white_list": True,
            "id_whitelist": ["group-1"],
        }
    }

    assert _simulate_waking_check_with_cfg([filter_], event, cfg) is False
    plugin._schedule_passive_group_capture.assert_called_once_with(event)


def test_passive_group_capture_filter_real_registration_constructor_uses_active_plugin():
    from astrbot_plugin_livingmemory.main import _set_active_plugin

    plugin = _make_plugin()
    event = _make_event()
    _set_active_plugin(plugin)
    try:
        filter_ = PassiveGroupCaptureFilter(False)

        assert filter_.raise_error is False
        assert _simulate_waking_check([filter_], event) is False
        plugin._schedule_passive_group_capture.assert_called_once_with(event)
    finally:
        _set_active_plugin(None)


@pytest.mark.asyncio
async def test_passive_group_capture_skips_session_disabled_plugin():
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin

    plugin = Mock(spec=LivingMemoryPlugin)
    plugin._is_enabled_for_session = LivingMemoryPlugin._is_enabled_for_session.__get__(
        plugin,
        LivingMemoryPlugin,
    )

    with patch(
        "astrbot_plugin_livingmemory.main.sp.get_async",
        new=AsyncMock(
            return_value={
                "aiocqhttp:GroupMessage:group-1": {
                    "disabled_plugins": ["LivingMemory"]
                }
            }
        ),
    ):
        assert (
            await plugin._is_enabled_for_session("aiocqhttp:GroupMessage:group-1")
            is False
        )


@pytest.mark.asyncio
async def test_passive_group_capture_skips_disabled_session():
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin

    plugin = Mock(spec=LivingMemoryPlugin)
    plugin._is_session_enabled = LivingMemoryPlugin._is_session_enabled.__get__(
        plugin,
        LivingMemoryPlugin,
    )

    with patch(
        "astrbot_plugin_livingmemory.main.sp.get_async",
        new=AsyncMock(return_value={"session_enabled": False}),
    ):
        assert (
            await plugin._is_session_enabled("aiocqhttp:GroupMessage:group-1")
            is False
        )


@pytest.mark.asyncio
async def test_passive_group_capture_allows_session_without_disable():
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin

    plugin = Mock(spec=LivingMemoryPlugin)
    plugin._is_enabled_for_session = LivingMemoryPlugin._is_enabled_for_session.__get__(
        plugin,
        LivingMemoryPlugin,
    )

    with patch(
        "astrbot_plugin_livingmemory.main.sp.get_async",
        new=AsyncMock(return_value={}),
    ):
        assert (
            await plugin._is_enabled_for_session("aiocqhttp:GroupMessage:group-1")
            is True
        )
