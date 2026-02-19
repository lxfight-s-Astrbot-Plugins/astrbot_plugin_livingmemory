"""
Tests for config manager and validator behavior.
"""

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import validate_config


def test_config_manager_loads_defaults() -> None:
    manager = ConfigManager()
    config = manager.get_all()

    assert isinstance(config, dict)
    assert manager.get("recall_engine.top_k") == 5
    assert manager.get("fusion_strategy.rrf_k") == 60
    assert manager.get("session_manager.max_sessions") == 100


def test_config_manager_supports_nested_get_and_default() -> None:
    manager = ConfigManager({"recall_engine": {"top_k": 9}})

    assert manager.get("recall_engine.top_k") == 9
    assert manager.get("recall_engine.unknown", "fallback") == "fallback"
    assert manager.get("missing.path", 123) == 123


def test_config_manager_sections_and_properties() -> None:
    manager = ConfigManager({"provider_settings": {"llm_provider_id": "x"}})

    assert manager.get_section("provider_settings")["llm_provider_id"] == "x"
    assert isinstance(manager.provider_settings, dict)
    assert isinstance(manager.webui_settings, dict)
    assert isinstance(manager.session_manager, dict)
    assert isinstance(manager.recall_engine, dict)
    assert isinstance(manager.reflection_engine, dict)
    assert isinstance(manager.filtering_settings, dict)


def test_invalid_user_config_falls_back_to_defaults() -> None:
    # Invalid type for top_k -> validation fails -> manager falls back to defaults.
    manager = ConfigManager({"recall_engine": {"top_k": "invalid"}})
    assert manager.get("recall_engine.top_k") == 5


def test_validate_config_accepts_merged_model_shape() -> None:
    config = validate_config(
        {
            "recall_engine": {"top_k": 8},
            "reflection_engine": {"summary_trigger_rounds": 4},
        }
    )

    assert config.recall_engine.top_k == 8
    assert config.reflection_engine.summary_trigger_rounds == 4
