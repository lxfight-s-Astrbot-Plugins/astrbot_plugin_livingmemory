"""Tests for plugin LLM tool registration."""

from unittest.mock import Mock

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.tools import (
    AgenticMemorySearchTool,
    MemoryMemorizeTool,
    MemoryReadTool,
    MemorySearchTool,
)
from astrbot_plugin_livingmemory.main import (
    LivingMemoryPlugin,
    _parse_version,
    _version_lt,
)


def test_parse_version_accepts_source_and_prerelease_versions():
    assert _parse_version("4.25.2") == (4, 25, 2)
    assert _parse_version("v4.25.2-beta.1") == (4, 25, 2)
    assert _parse_version("not-a-version") == ()


def test_version_lt_pads_version_segments():
    assert _version_lt("4.24.1", "4.24.2") is True
    assert _version_lt("4.25", "4.25.0") is False
    assert _version_lt("4.25.2", "4.24.2") is False
    assert _version_lt("unknown", "4.24.2") is False


def test_register_llm_tools_is_idempotent():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(
        {"agent_tools": {"enable_recall_tool": True, "enable_memorize_tool": True}}
    )
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()
    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tools = plugin.context.add_llm_tools.call_args.args
    tools_by_name = {tool.name: tool for tool in tools}
    assert set(tools_by_name) == {
        "recall_long_term_memory",
        "memorize_long_term_memory",
    }
    assert isinstance(tools_by_name["recall_long_term_memory"], MemorySearchTool)
    assert isinstance(tools_by_name["memorize_long_term_memory"], MemoryMemorizeTool)
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_defaults_only_recall():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == ["recall_long_term_memory"]
    assert isinstance(tools[0], MemorySearchTool)
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_no_memory_engine():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = None
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is False


def test_register_llm_tools_no_memory_processor():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = None
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is False


def test_register_llm_tools_respects_recall_tool_disabled():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(
        {"agent_tools": {"enable_recall_tool": False, "enable_memorize_tool": True}}
    )
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == ["memorize_long_term_memory"]
    assert isinstance(tools[0], MemoryMemorizeTool)
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_respects_memorize_tool_disabled():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(
        {"agent_tools": {"enable_recall_tool": True, "enable_memorize_tool": False}}
    )
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == ["recall_long_term_memory"]
    assert isinstance(tools[0], MemorySearchTool)
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_respects_all_tools_disabled():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(
        {"agent_tools": {"enable_recall_tool": False, "enable_memorize_tool": False}}
    )
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is True


def _make_plugin(agent_tools):
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager({"agent_tools": agent_tools})
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False
    return plugin


def test_beta_off_registers_baseline_search_tool_only():
    plugin = _make_plugin(
        {"enable_recall_tool": True, "enable_agentic_recall_beta": False}
    )
    plugin._register_agent_tools_if_needed()

    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == ["recall_long_term_memory"]
    assert isinstance(tools[0], MemorySearchTool)
    assert not isinstance(tools[0], AgenticMemorySearchTool)
    # 基线 schema：query 必填，无 target 参数
    assert plugin.context.add_llm_tools.call_args.args[0].parameters["required"] == [
        "query"
    ]
    assert "target" not in tools[0].parameters["properties"]


def test_beta_on_registers_agentic_search_and_read_tools():
    plugin = _make_plugin(
        {"enable_recall_tool": True, "enable_agentic_recall_beta": True}
    )
    plugin._register_agent_tools_if_needed()

    tools = {tool.name: tool for tool in plugin.context.add_llm_tools.call_args.args}
    assert set(tools) == {
        "recall_long_term_memory",
        "read_memory_evidence",
    }
    assert isinstance(tools["recall_long_term_memory"], AgenticMemorySearchTool)
    assert isinstance(tools["read_memory_evidence"], MemoryReadTool)
    # Beta schema：query 非必填，支持 target / exclude_ids / 时间范围 / search_offset
    properties = tools["recall_long_term_memory"].parameters["properties"]
    for field in (
        "target",
        "start_time",
        "end_time",
        "exclude_ids",
        "search_offset",
    ):
        assert field in properties
    assert tools["recall_long_term_memory"].parameters["required"] == []


def test_beta_cannot_bypass_recall_master_switch():
    plugin = _make_plugin(
        {"enable_recall_tool": False, "enable_agentic_recall_beta": True}
    )
    plugin._register_agent_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is True


def test_memorize_tool_independent_of_beta():
    plugin = _make_plugin(
        {
            "enable_recall_tool": True,
            "enable_agentic_recall_beta": True,
            "enable_memorize_tool": True,
        }
    )
    plugin._register_agent_tools_if_needed()

    tools = {tool.name: tool for tool in plugin.context.add_llm_tools.call_args.args}
    assert set(tools) == {
        "recall_long_term_memory",
        "read_memory_evidence",
        "memorize_long_term_memory",
    }
    assert isinstance(tools["memorize_long_term_memory"], MemoryMemorizeTool)


def test_beta_missing_config_defaults_to_baseline():
    plugin = _make_plugin({"enable_recall_tool": True})
    plugin._register_agent_tools_if_needed()

    tools = plugin.context.add_llm_tools.call_args.args
    assert [tool.name for tool in tools] == ["recall_long_term_memory"]
    assert isinstance(tools[0], MemorySearchTool)


def test_reload_lifecycle_off_on_off_restores_tool_set():
    """模拟宿主重载：每个实例重新走注册流程，工具集合随配置往返恢复。"""
    off_config = {
        "enable_recall_tool": True,
        "enable_agentic_recall_beta": False,
        "enable_memorize_tool": False,
    }
    on_config = {
        "enable_recall_tool": True,
        "enable_agentic_recall_beta": True,
        "enable_memorize_tool": False,
    }

    def fresh_plugin(agent_tools):
        # 重载会构造新插件实例并重新初始化，_llm_tools_registered 复位
        plugin = _make_plugin(agent_tools)
        return plugin

    # 关 -> 开 -> 关
    state_off = fresh_plugin(off_config)
    state_off._register_agent_tools_if_needed()
    off_names = {t.name for t in state_off.context.add_llm_tools.call_args.args}
    assert off_names == {"recall_long_term_memory"}

    state_on = fresh_plugin(on_config)
    state_on._register_agent_tools_if_needed()
    on_names = {t.name for t in state_on.context.add_llm_tools.call_args.args}
    assert on_names == {"recall_long_term_memory", "read_memory_evidence"}

    state_off_again = fresh_plugin(off_config)
    state_off_again._register_agent_tools_if_needed()
    off_again_names = {
        t.name for t in state_off_again.context.add_llm_tools.call_args.args
    }
    assert off_again_names == {"recall_long_term_memory"}
    # 深读工具不再出现，且搜索工具恢复为基线实现
    tools = state_off_again.context.add_llm_tools.call_args.args
    assert isinstance(tools[0], MemorySearchTool)
