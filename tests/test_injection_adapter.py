"""
Tests for InjectionAdapter — Provider/Model automatic injection strategy selection.
"""

from unittest.mock import Mock

import pytest

from astrbot_plugin_livingmemory.core.utils.injection_adapter import InjectionAdapter


@pytest.fixture
def adapter():
    return InjectionAdapter()


def test_resolve_non_fake_mode_unchanged(adapter):
    """非 fake_tool_call 模式应原样返回。"""
    assert adapter.resolve(None, "system_prompt") == ("system_prompt", None)
    assert adapter.resolve(None, "user_message_before") == ("user_message_before", None)


def test_resolve_gemini_by_provider_type(adapter):
    """provider type 为 googlegenai_chat_completion 时应降级为 user_message_before。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "googlegenai_chat_completion"}
    gemini_provider.get_model = Mock(return_value="gemini-2.5-pro")

    mode, reason = adapter.resolve(gemini_provider, "fake_tool_call")

    assert mode == "user_message_before"
    assert reason is not None
    assert "gemini" in reason.lower()


def test_resolve_gemini_by_model_name(adapter):
    """model 名包含 gemini 时应降级为 user_message_before。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "some_other_type"}
    gemini_provider.get_model = Mock(return_value="gemini-1.5-flash")

    mode, reason = adapter.resolve(gemini_provider, "fake_tool_call")

    assert mode == "user_message_before"
    assert reason is not None


def test_resolve_openai_unchanged(adapter):
    """OpenAI 风格 provider 不应降级。"""
    openai_provider = Mock()
    openai_provider.provider_config = {"type": "openai_chat_completion"}
    openai_provider.get_model = Mock(return_value="gpt-4o")

    mode, reason = adapter.resolve(openai_provider, "fake_tool_call")

    assert mode == "fake_tool_call"
    assert reason is None


def test_resolve_no_provider(adapter):
    """获取不到 provider 时不应降级。"""
    mode, reason = adapter.resolve(None, "fake_tool_call")

    assert mode == "fake_tool_call"
    assert reason is None


def test_resolve_returns_reason_on_fallback(adapter):
    """降级时应返回 fallback reason，由调用方统一记录日志。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "googlegenai_chat_completion"}
    gemini_provider.get_model = Mock(return_value="gemini-pro")

    mode, reason = adapter.resolve(gemini_provider, "fake_tool_call")

    assert mode == "user_message_before"
    assert reason is not None
    assert "fake_tool_call is not fully compatible" in reason
