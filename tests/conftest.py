"""
pytest 配置文件
提供测试夹具和基础运行环境。
"""

import asyncio
import logging
import sys
import tempfile
import types
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = Path(__file__).resolve().parents[2]
ASTRBOT_ROOT = Path(__file__).resolve().parents[4]

for candidate in (PROJECT_ROOT, PLUGINS_DIR, ASTRBOT_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)


def _ensure_plugin_package_alias() -> None:
    """将当前源码目录暴露为测试中使用的包名。"""
    if "astrbot_plugin_livingmemory" in sys.modules:
        return

    plugin_pkg = types.ModuleType("astrbot_plugin_livingmemory")
    plugin_pkg.__path__ = [str(PROJECT_ROOT)]
    plugin_pkg.__file__ = str(PROJECT_ROOT / "__init__.py")
    sys.modules["astrbot_plugin_livingmemory"] = plugin_pkg


def _install_optional_dependency_stubs() -> None:
    """为单元测试安装轻量的第三方依赖桩。"""
    try:
        import aiosqlite  # type: ignore  # noqa: F401
    except Exception:
        aiosqlite_mod = types.ModuleType("aiosqlite")

        class Connection:
            pass

        async def connect(*args, **kwargs):
            return Connection()

        aiosqlite_mod.Connection = Connection
        aiosqlite_mod.connect = connect
        sys.modules["aiosqlite"] = aiosqlite_mod

    try:
        import jieba  # type: ignore  # noqa: F401
    except Exception:
        jieba_mod = types.ModuleType("jieba")
        jieba_mod.cut = lambda text, cut_all=False: list(text) if text else []
        jieba_mod.lcut = lambda text, cut_all=False: list(text) if text else []
        sys.modules["jieba"] = jieba_mod

    try:
        import pytz  # type: ignore  # noqa: F401
    except Exception:
        pytz_mod = types.ModuleType("pytz")
        pytz_mod.timezone = lambda name: None
        sys.modules["pytz"] = pytz_mod


def _install_astrbot_stubs() -> None:
    """安装满足当前单元测试所需的最小 AstrBot 桩模块。"""
    logger = logging.getLogger("astrbot-test")
    logger.addHandler(logging.NullHandler())

    class _StorageProxy:
        async def get_async(self, *args, **kwargs):
            return None

    class _Filter:
        def on_llm_request(self):
            return lambda func: func

        def on_llm_response(self):
            return lambda func: func

        def after_message_sent(self):
            return lambda func: func

        def platform_adapter_type(self, *args, **kwargs):
            return lambda func: func

    class AstrMessageEvent:
        pass

    class MessageEventResult:
        pass

    class MessageType(Enum):
        FRIEND_MESSAGE = "friend"
        GROUP_MESSAGE = "group"

    @dataclass
    class ProviderRequest:
        prompt: str | None = None
        session_id: str | None = ""
        image_urls: list = field(default_factory=list)
        audio_urls: list = field(default_factory=list)
        extra_user_content_parts: list = field(default_factory=list)
        func_tool: object | None = None
        contexts: list = field(default_factory=list)
        system_prompt: str = ""
        conversation: object | None = None
        tool_calls_result: list | None = None
        model: str | None = None

    @dataclass
    class LLMResponse:
        role: str = "assistant"
        result_chain: object | None = None
        tools_call_args: list = field(default_factory=list)
        tools_call_name: list | None = None
        tools_call_ids: list = field(default_factory=list)
        tools_call_extra_content: dict | None = None
        completion_text: str = ""

    class Context:
        pass

    class Star:
        pass

    class StarTools:
        pass

    class FunctionTool:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class ToolSet:
        pass

    ToolExecResult = str

    class BaseFunctionToolExecutor:
        pass

    class ContextWrapper:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class AstrAgentContext:
        pass

    class AstrBotConfig:
        pass

    class EmbeddingProvider:
        pass

    class Provider:
        pass

    class SQLiteDatabase:
        pass

    class FaissVecDB:
        pass

    class _BaseComponent:
        type = "component"

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Plain(_BaseComponent):
        type = "plain"

        def __init__(self, text=""):
            super().__init__(text=text)

    class Image(_BaseComponent):
        type = "image"

    class Record(_BaseComponent):
        type = "record"

    class Video(_BaseComponent):
        type = "video"

    class File(_BaseComponent):
        type = "file"

        def __init__(self, name=""):
            super().__init__(name=name)

    class Face(_BaseComponent):
        type = "face"

        def __init__(self, id=""):
            super().__init__(id=id)

    class At(_BaseComponent):
        type = "at"

        def __init__(self, qq=""):
            super().__init__(qq=qq)

    class AtAll(At):
        type = "atall"

    class Forward(_BaseComponent):
        type = "forward"

    class Reply(_BaseComponent):
        type = "reply"

        def __init__(self, message_str=""):
            super().__init__(message_str=message_str)

    def register(*args, **kwargs):
        return lambda obj: obj

    def register_agent(*args, **kwargs):
        return lambda obj: obj

    def register_llm_tool(*args, **kwargs):
        return lambda obj: obj

    class PermissionType(Enum):
        ADMIN = "admin"

    def permission_type(*args, **kwargs):
        return lambda func: func

    def _package(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__path__ = []
        return module

    astrbot_mod = _package("astrbot")
    astrbot_mod.logger = logger

    api_mod = _package("astrbot.api")
    api_mod.logger = logger
    api_mod.sp = _StorageProxy()

    event_mod = _package("astrbot.api.event")
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = MessageEventResult
    event_mod.filter = _Filter()

    event_filter_mod = types.ModuleType("astrbot.api.event.filter")
    event_filter_mod.PermissionType = PermissionType
    event_filter_mod.permission_type = permission_type

    platform_mod = types.ModuleType("astrbot.api.platform")
    platform_mod.MessageType = MessageType

    provider_mod = types.ModuleType("astrbot.api.provider")
    provider_mod.ProviderRequest = ProviderRequest
    provider_mod.LLMResponse = LLMResponse

    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools
    star_mod.register = register

    core_mod = _package("astrbot.core")
    core_mod.sp = api_mod.sp
    core_mod.html_renderer = None

    core_db_mod = _package("astrbot.core.db")
    core_vec_db_mod = _package("astrbot.core.db.vec_db")
    core_faiss_pkg_mod = _package("astrbot.core.db.vec_db.faiss_impl")
    core_faiss_vec_mod = types.ModuleType("astrbot.core.db.vec_db.faiss_impl.vec_db")
    core_faiss_vec_mod.FaissVecDB = FaissVecDB

    core_agent_mod = _package("astrbot.core.agent")
    core_agent_tool_mod = types.ModuleType("astrbot.core.agent.tool")
    core_agent_tool_mod.FunctionTool = FunctionTool
    core_agent_tool_mod.ToolSet = ToolSet
    core_agent_tool_mod.ToolExecResult = ToolExecResult

    core_agent_executor_mod = types.ModuleType("astrbot.core.agent.tool_executor")
    core_agent_executor_mod.BaseFunctionToolExecutor = BaseFunctionToolExecutor

    core_agent_run_context_mod = types.ModuleType("astrbot.core.agent.run_context")
    core_agent_run_context_mod.ContextWrapper = ContextWrapper

    core_astr_agent_context_mod = types.ModuleType("astrbot.core.astr_agent_context")
    core_astr_agent_context_mod.AstrAgentContext = AstrAgentContext

    core_config_mod = _package("astrbot.core.config")
    core_config_astrbot_mod = types.ModuleType("astrbot.core.config.astrbot_config")
    core_config_astrbot_mod.AstrBotConfig = AstrBotConfig

    core_star_mod = _package("astrbot.core.star")
    core_star_register_mod = types.ModuleType("astrbot.core.star.register")
    core_star_register_mod.register_agent = register_agent
    core_star_register_mod.register_llm_tool = register_llm_tool

    core_provider_mod = _package("astrbot.core.provider")
    core_provider_provider_mod = types.ModuleType("astrbot.core.provider.provider")
    core_provider_provider_mod.EmbeddingProvider = EmbeddingProvider
    core_provider_provider_mod.Provider = Provider

    core_sqlite_mod = types.ModuleType("astrbot.core.db.sqlite")
    core_sqlite_mod.SQLiteDatabase = SQLiteDatabase

    core_message_mod = _package("astrbot.core.message")
    core_message_components_mod = types.ModuleType("astrbot.core.message.components")
    core_message_components_mod.Plain = Plain
    core_message_components_mod.Image = Image
    core_message_components_mod.Record = Record
    core_message_components_mod.Video = Video
    core_message_components_mod.File = File
    core_message_components_mod.Face = Face
    core_message_components_mod.At = At
    core_message_components_mod.AtAll = AtAll
    core_message_components_mod.Forward = Forward
    core_message_components_mod.Reply = Reply

    sys.modules.update(
        {
            "astrbot": astrbot_mod,
            "astrbot.api": api_mod,
            "astrbot.api.event": event_mod,
            "astrbot.api.event.filter": event_filter_mod,
            "astrbot.api.platform": platform_mod,
            "astrbot.api.provider": provider_mod,
            "astrbot.api.star": star_mod,
            "astrbot.core": core_mod,
            "astrbot.core.db": core_db_mod,
            "astrbot.core.db.sqlite": core_sqlite_mod,
            "astrbot.core.db.vec_db": core_vec_db_mod,
            "astrbot.core.db.vec_db.faiss_impl": core_faiss_pkg_mod,
            "astrbot.core.db.vec_db.faiss_impl.vec_db": core_faiss_vec_mod,
            "astrbot.core.agent": core_agent_mod,
            "astrbot.core.agent.tool": core_agent_tool_mod,
            "astrbot.core.agent.tool_executor": core_agent_executor_mod,
            "astrbot.core.agent.run_context": core_agent_run_context_mod,
            "astrbot.core.astr_agent_context": core_astr_agent_context_mod,
            "astrbot.core.config": core_config_mod,
            "astrbot.core.config.astrbot_config": core_config_astrbot_mod,
            "astrbot.core.star": core_star_mod,
            "astrbot.core.star.register": core_star_register_mod,
            "astrbot.core.provider": core_provider_mod,
            "astrbot.core.provider.provider": core_provider_provider_mod,
            "astrbot.core.message": core_message_mod,
            "astrbot.core.message.components": core_message_components_mod,
        }
    )

    astrbot_mod.api = api_mod
    astrbot_mod.core = core_mod
    api_mod.event = event_mod
    api_mod.platform = platform_mod
    api_mod.provider = provider_mod
    api_mod.star = star_mod
    api_mod.FunctionTool = FunctionTool
    api_mod.ToolSet = ToolSet
    api_mod.BaseFunctionToolExecutor = BaseFunctionToolExecutor
    api_mod.AstrBotConfig = AstrBotConfig
    api_mod.agent = register_agent
    api_mod.llm_tool = register_llm_tool

    core_mod.db = core_db_mod
    core_mod.agent = core_agent_mod
    core_mod.config = core_config_mod
    core_mod.star = core_star_mod
    core_mod.provider = core_provider_mod
    core_mod.message = core_message_mod
    core_db_mod.sqlite = core_sqlite_mod
    core_db_mod.vec_db = core_vec_db_mod
    core_vec_db_mod.faiss_impl = core_faiss_pkg_mod
    core_faiss_pkg_mod.vec_db = core_faiss_vec_mod
    core_agent_mod.tool = core_agent_tool_mod
    core_agent_mod.tool_executor = core_agent_executor_mod
    core_agent_mod.run_context = core_agent_run_context_mod
    core_config_mod.astrbot_config = core_config_astrbot_mod
    core_star_mod.register = core_star_register_mod
    core_provider_mod.provider = core_provider_provider_mod
    core_message_mod.components = core_message_components_mod


_ensure_plugin_package_alias()
_install_optional_dependency_stubs()

try:
    import astrbot.api  # type: ignore  # noqa: F401
    from astrbot.core.db.vec_db.faiss_impl.vec_db import (  # type: ignore  # noqa: F401
        FaissVecDB,
    )
except Exception:
    _install_astrbot_stubs()


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """创建临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_db_path(temp_dir: Path) -> str:
    """创建测试数据库路径"""
    return str(temp_dir / "test_livingmemory.db")


@pytest.fixture
def test_index_path(temp_dir: Path) -> str:
    """创建测试索引路径"""
    return str(temp_dir / "test_livingmemory.index")


@pytest.fixture
def test_config() -> dict:
    """创建测试配置"""
    return {
        "rrf_k": 60,
        "decay_rate": 0.01,
        "importance_weight": 1.0,
        "fallback_enabled": True,
        "cleanup_days_threshold": 30,
        "cleanup_importance_threshold": 0.3,
    }


@pytest.fixture
def mock_event():
    """Create a minimal mock event compatible with command/event handlers."""

    class _Event:
        unified_msg_origin = "test:private:session-1"

        def plain_result(self, message):
            return message

        def get_message_type(self):
            return None

        def get_sender_id(self):
            return "user-1"

        def get_self_id(self):
            return "bot-1"

        def get_sender_name(self):
            return "Tester"

        def get_message_str(self):
            return "hello"

        def get_messages(self):
            return []

        def get_platform_name(self):
            return "test"

    return _Event()
