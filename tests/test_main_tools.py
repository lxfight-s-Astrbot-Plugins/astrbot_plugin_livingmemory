"""Tests for plugin LLM tool registration."""

from unittest.mock import Mock

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.tools import MemorySearchTool
from astrbot_plugin_livingmemory.main import LivingMemoryPlugin


def test_register_llm_tools_is_idempotent():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = Mock()
    plugin._llm_tools_registered = False

    plugin._register_llm_tools_if_needed()
    plugin._register_llm_tools_if_needed()

    plugin.context.add_llm_tools.assert_called_once()
    tool = plugin.context.add_llm_tools.call_args.args[0]
    assert isinstance(tool, MemorySearchTool)
    assert plugin._llm_tools_registered is True


def test_register_llm_tools_no_memory_engine():
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager()
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = None
    plugin._llm_tools_registered = False

    plugin._register_llm_tools_if_needed()

    plugin.context.add_llm_tools.assert_not_called()
