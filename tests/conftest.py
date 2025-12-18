"""
pytest 配置文件
提供测试夹具和工具函数
"""

import asyncio
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest


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
