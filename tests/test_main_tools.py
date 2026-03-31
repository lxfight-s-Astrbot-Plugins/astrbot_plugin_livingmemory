"""Tests for plugin LLM tool registration."""

import sys
from pathlib import Path
from unittest.mock import Mock

ASTRBOT_ROOT = Path(__file__).resolve().parents[4]
astrbot_root_str = str(ASTRBOT_ROOT)
if astrbot_root_str not in sys.path:
    sys.path.insert(0, astrbot_root_str)

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
