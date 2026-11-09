"""
Tests for config manager and validator behavior.
"""

from unittest.mock import patch

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import validate_config
from astrbot_plugin_livingmemory.core.base.exceptions import ConfigurationError


class _WritableConfig(dict):
    """Minimal AstrBotConfig stand-in that records save_config calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


def test_config_manager_loads_defaults() -> None:
    manager = ConfigManager()
    config = manager.get_all()

    assert isinstance(config, dict)
    assert "sparse_retriever" not in config
    assert "dense_retriever" not in config
    assert manager.get("recall_engine.top_k") == 5
    assert manager.get("recall_engine.min_importance_for_retrieval") == 0.0
    assert manager.get("recall_engine.min_similarity_for_retrieval") == 0.45
    assert manager.get("recall_engine.recent_memory_count") == 0
    assert manager.get("recall_engine.memory_type_filter") == "all"
    assert manager.get("recall_engine.recent_context_max_age_seconds") == 7200
    assert manager.get("fusion_strategy.rrf_k") == 60
    assert manager.get("session_manager.max_sessions") == 100
    assert manager.get("session_manager.max_messages_per_session") == 1000
    assert manager.get("session_manager.cleanup_batch_size") == 50
    assert manager.get("reflection_engine.save_original_conversation") is None


def test_config_manager_supports_nested_get_and_default() -> None:
    manager = ConfigManager({"recall_engine": {"top_k": 9}})

    assert manager.get("recall_engine.top_k") == 9
    assert manager.get("recall_engine.unknown", "fallback") == "fallback"
    assert manager.get("missing.path", 123) == 123


def test_config_manager_sections_and_properties() -> None:
    manager = ConfigManager({"provider_settings": {"llm_provider_id": "x"}})

    assert manager.get_section("provider_settings")["llm_provider_id"] == "x"
    assert isinstance(manager.provider_settings, dict)
    assert isinstance(manager.session_manager, dict)
    assert isinstance(manager.recall_engine, dict)
    assert isinstance(manager.reflection_engine, dict)
    assert isinstance(manager.filtering_settings, dict)


def test_invalid_config_value_fails_fast() -> None:
    # 非法类型不再静默回退默认值，而是直接拒绝启动。
    with pytest.raises(ConfigurationError):
        ConfigManager({"recall_engine": {"top_k": "invalid"}})


def test_out_of_range_config_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        ConfigManager(
            {
                "recall_engine": {"top_k": 60, "max_k": 0},
                "session_manager": {"max_sessions": 7, "session_ttl": 30},
                "backup_settings": {"keep_days": 9999},
            }
        )


def test_pattern_violation_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        ConfigManager({"recall_engine": {"memory_type_filter": "unknown"}})


def test_structural_broken_section_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        ConfigManager({"session_manager": "broken"})


def test_preserves_extra_host_config_fields() -> None:
    manager = ConfigManager(
        {
            "bot_language": "en",
            "backup_settings": {"enabled": True, "keep_days": 7},
        }
    )

    assert manager.get("bot_language") == "en"
    assert manager.get("backup_settings.enabled") is True
    assert manager.get("backup_settings.keep_days") == 7


def test_bool_string_coerced() -> None:
    manager = ConfigManager(
        {
            "recall_engine": {
                "fallback_to_vector": "false",
                "search_cache_enabled": 1,
            }
        }
    )

    assert manager.get("recall_engine.fallback_to_vector") is False
    assert manager.get("recall_engine.search_cache_enabled") is True


def test_validate_config_accepts_merged_model_shape() -> None:
    config = validate_config(
        {
            "recall_engine": {"top_k": 8},
            "reflection_engine": {"summary_trigger_rounds": 4},
        }
    )

    assert config.recall_engine.top_k == 8
    assert config.reflection_engine.summary_trigger_rounds == 4


def test_validate_config_accepts_retrieval_importance_threshold() -> None:
    config = validate_config(
        {"recall_engine": {"min_importance_for_retrieval": 0.65}}
    )

    assert config.recall_engine.min_importance_for_retrieval == 0.65


def test_validate_config_accepts_retrieval_memory_policies() -> None:
    config = validate_config(
        {
            "recall_engine": {
                "min_similarity_for_retrieval": 0.72,
                "recent_memory_count": 3,
                "recent_memory_max_age_hours": 48,
                "memory_type_filter": "event_only",
            },
            "reflection_engine": {"include_source_time_tags": False},
            "forgetting_agent": {"auto_archived_enabled": True},
            "importance_decay": {"protected_importance_threshold": 0.85},
        }
    )

    assert config.recall_engine.min_similarity_for_retrieval == 0.72
    assert config.recall_engine.recent_memory_count == 3
    assert config.recall_engine.memory_type_filter == "event_only"
    assert config.reflection_engine.include_source_time_tags is False
    assert config.forgetting_agent.auto_archived_enabled is True
    assert config.importance_decay.protected_importance_threshold == 0.85


def test_validate_config_accepts_recent_context_max_age() -> None:
    config = validate_config(
        {"recall_engine": {"recent_context_max_age_seconds": 3600}}
    )

    assert config.recall_engine.recent_context_max_age_seconds == 3600


def test_validate_config_accepts_legacy_injection_modes() -> None:
    """旧注入枚举值仍需通过校验，避免老配置升级后无法启动。"""
    legacy_modes = [
        "user_message_before",
        "user_message_after",
        "fake_tool_call",
        "fake_tool_call_deepseek_v4",
        "system_prompt",
    ]

    for mode in legacy_modes:
        config = validate_config({"recall_engine": {"injection_method": mode}})
        assert config.recall_engine.injection_method == mode


def test_unknown_injection_mode_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        ConfigManager({"recall_engine": {"injection_method": "unknown_mode"}})


def test_legacy_injection_mode_normalized_with_single_warning() -> None:
    raw = _WritableConfig({"recall_engine": {"injection_method": "fake_tool_call"}})

    with patch(
        "astrbot_plugin_livingmemory.core.base.config_manager.logger.warning"
    ) as warning:
        manager = ConfigManager(raw)

        assert manager.get("recall_engine.injection_method") == "extra_user_content"
        warning.assert_called_once()
        assert "fake_tool_call" in str(warning.call_args.args)

        # 宿主配置未回写，重新加载时不再重复提示
        manager.apply_updates({"recall_engine.top_k": 7})
        assert manager.get("recall_engine.top_k") == 7
        warning.assert_called_once()


def test_config_manager_graph_memory_property() -> None:
    manager = ConfigManager(
        {
            "graph_memory": {
                "enabled": False,
                "graph_route_weight": 0.35,
            }
        }
    )

    assert isinstance(manager.graph_memory, dict)
    assert manager.graph_memory["enabled"] is False
    assert manager.get("graph_memory.graph_route_weight") == 0.35
    assert manager.get("graph_memory.document_route_weight") == 0.65
