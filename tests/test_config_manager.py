"""
测试ConfigManager
"""

import pytest

from core.config_manager import ConfigManager


def test_config_manager_initialization():
    """测试ConfigManager初始化"""
    config = ConfigManager()
    assert config is not None
    assert isinstance(config.get_all(), dict)


def test_config_manager_get():
    """测试配置获取"""
    config = ConfigManager({"test_key": "test_value"})

    # 测试获取存在的键
    value = config.get("test_key")
    assert value == "test_value"

    # 测试获取不存在的键
    value = config.get("non_existent_key", "default")
    assert value == "default"


def test_config_manager_nested_get():
    """测试嵌套配置获取"""
    config = ConfigManager({
        "section1": {
            "key1": "value1"
        }
    })

    # 测试嵌套键访问
    value = config.get("section1.key1")
    assert value == "value1"

    # 测试不存在的嵌套键
    value = config.get("section1.key2", "default")
    assert value == "default"


def test_config_manager_get_section():
    """测试配置节获取"""
    config = ConfigManager({
        "provider_settings": {
            "llm_provider_id": "test_provider"
        }
    })

    section = config.get_section("provider_settings")
    assert isinstance(section, dict)
    assert section.get("llm_provider_id") == "test_provider"


def test_config_manager_properties():
    """测试配置属性"""
    config = ConfigManager()

    # 测试各个配置节属性
    assert isinstance(config.provider_settings, dict)
    assert isinstance(config.webui_settings, dict)
    assert isinstance(config.session_manager, dict)
    assert isinstance(config.recall_engine, dict)
    assert isinstance(config.reflection_engine, dict)
    assert isinstance(config.filtering_settings, dict)
