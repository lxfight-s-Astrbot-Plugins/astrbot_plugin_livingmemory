"""
pytest 配置文件
提供测试夹具和工具函数
"""

import asyncio
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# Ensure plugin modules are importable during test module collection.
PLUGINS_DIR = Path(__file__).resolve().parents[2]
plugins_dir_str = str(PLUGINS_DIR)
if plugins_dir_str not in sys.path:
    sys.path.insert(0, plugins_dir_str)


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
