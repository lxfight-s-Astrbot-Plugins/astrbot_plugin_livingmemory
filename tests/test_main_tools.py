"""Tests for plugin LLM tool registration."""

from unittest.mock import Mock

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.tools import MemoryMemorizeTool, MemorySearchTool
from astrbot_plugin_livingmemory.main import LivingMemoryPlugin


def test_register_llm_tools_is_idempotent():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_llm_tools_if_needed()
    plugin._register_llm_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tools = plugin.context.add_llm_tools.call_args.args
    assert len(tools) == 2
    assert isinstance(tools[0], MemorySearchTool)
    assert tools[0].name == "recall_long_term_memory"
    assert isinstance(tools[1], MemoryMemorizeTool)
    assert tools[1].name == "memorize_long_term_memory"
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_no_memory_engine():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = None
    plugin.initializer.memory_processor = Mock()
    plugin._llm_tools_registered = False

    plugin._register_llm_tools_if_needed()

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

    plugin._register_llm_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
    assert plugin._llm_tools_registered is False
