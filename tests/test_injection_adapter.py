"""
Tests for InjectionAdapter — legacy injection modes mapped to the single add-only mode.
"""

import pytest

from astrbot_plugin_livingmemory.core.utils.injection_adapter import InjectionAdapter


@pytest.fixture
def adapter():
    return InjectionAdapter()


def test_resolve_supported_mode_unchanged(adapter):
    """唯一支持的 extra_user_content 应原样返回，无降级原因。"""
    assert adapter.resolve("extra_user_content") == ("extra_user_content", None)


def test_resolve_empty_mode_falls_back_silently(adapter):
    """空配置应直接使用默认注入方式，不产生回退日志。"""
    assert adapter.resolve("") == ("extra_user_content", None)
    assert adapter.resolve(None) == ("extra_user_content", None)


def test_resolve_system_prompt_falls_back(adapter):
    """system_prompt 已废弃，应自动回退到 extra_user_content。"""
    mode, reason = adapter.resolve("system_prompt")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "system_prompt" in reason
    assert "extra_user_content" in reason


def test_resolve_user_message_before_falls_back(adapter):
    """user_message_before 已废弃（改写用户消息并持久化），应回退。"""
    mode, reason = adapter.resolve("user_message_before")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "user_message_before" in reason
    assert "extra_user_content" in reason


def test_resolve_user_message_after_falls_back(adapter):
    """user_message_after 已废弃（改写用户消息并持久化），应回退。"""
    mode, reason = adapter.resolve("user_message_after")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "user_message_after" in reason
    assert "extra_user_content" in reason


def test_resolve_fake_tool_call_falls_back(adapter):
    """fake_tool_call 已废弃（伪造消息对会持久化进历史），应回退。"""
    mode, reason = adapter.resolve("fake_tool_call")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "fake_tool_call" in reason
    assert "extra_user_content" in reason


def test_resolve_deepseek_v4_mode_falls_back(adapter):
    """DeepSeek V4 专用模式已废弃，应自动回退到 extra_user_content。"""
    mode, reason = adapter.resolve("fake_tool_call_deepseek_v4")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "fake_tool_call_deepseek_v4" in reason
    assert "extra_user_content" in reason


def test_resolve_unknown_mode_falls_back(adapter):
    """未知注入方式应回退到 extra_user_content 并给出原因。"""
    mode, reason = adapter.resolve("some_new_mode")

    assert mode == "extra_user_content"
    assert reason is not None
    assert "some_new_mode" in reason


def test_resolve_does_not_need_provider(adapter):
    """废弃回退是纯配置映射，不应依赖 provider 实例。"""
    mode, reason = adapter.resolve("fake_tool_call")

    assert mode == "extra_user_content"
    assert reason is not None
