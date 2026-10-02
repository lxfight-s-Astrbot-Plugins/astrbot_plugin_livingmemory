"""Beta 配置开关的默认值、位置与顺序保护。"""

import json
from pathlib import Path

from astrbot_plugin_livingmemory.core.base.config_validator import AgentToolsConfig

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "_conf_schema.json"
)


def test_schema_places_beta_immediately_after_recall_tool():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    keys = list(schema["agent_tools"]["items"].keys())
    assert keys[:3] == [
        "enable_recall_tool",
        "enable_agentic_recall_beta",
        "enable_memorize_tool",
    ]


def test_beta_defaults_false_and_missing_field_off():
    config = AgentToolsConfig()
    assert config.enable_agentic_recall_beta is False
    # 旧配置只有主开关时，Beta 仍关闭
    legacy = AgentToolsConfig(enable_recall_tool=True)
    assert legacy.enable_agentic_recall_beta is False


def test_budget_defaults():
    config = AgentToolsConfig()
    assert config.agentic_recall_max_calls == 4
    assert config.agentic_recall_time_budget_seconds == 20
    assert config.agentic_recall_max_result_chars == 12000


def test_memorize_default_unchanged():
    config = AgentToolsConfig()
    assert config.enable_memorize_tool is False
    assert config.enable_recall_tool is True
