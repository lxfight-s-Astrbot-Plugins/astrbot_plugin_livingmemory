"""Extended tests for StopwordsManager to improve coverage."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.utils.stopwords_manager import (
    StopwordsManager,
    get_stopwords_manager,
)


@pytest.fixture
def stopwords_manager(tmp_path):
    """创建临时目录的stopwords manager"""
    return StopwordsManager(stopwords_dir=str(tmp_path))


@pytest.fixture
def builtin_stopwords_file(tmp_path):
    """创建模拟的内置停用词文件"""
    builtin_dir = tmp_path / "static" / "stopwords"
    builtin_dir.mkdir(parents=True, exist_ok=True)

    hit_file = builtin_dir / "stopwords_hit.txt"
    hit_file.write_text("的\n是\n在\n# 这是注释\n\n了\n", encoding="utf-8")

    return builtin_dir


class TestStopwordsManagerInit:
    """测试初始化"""

    def test_init_with_custom_dir(self, tmp_path):
        """测试使用自定义目录初始化"""
        custom_dir = tmp_path / "custom"
        manager = StopwordsManager(stopwords_dir=str(custom_dir))

        assert manager.custom_stopwords_dir == custom_dir
        assert custom_dir.exists()
        assert manager.stopwords == set()
        assert manager.custom_stopwords == set()

    def test_init_without_custom_dir(self):
        """测试不使用自定义目录初始化"""
        manager = StopwordsManager()

        assert manager.custom_stopwords_dir is None
        assert manager.stopwords == set()
        assert manager.custom_stopwords == set()


class TestLoadStopwords:
    """测试加载停用词"""

    @pytest.mark.asyncio
    async def test_load_hit_stopwords_from_builtin(self, tmp_path):
        """测试从内置目录加载hit停用词"""
        # 创建内置文件
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        # Mock builtin_stopwords_dir
        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        hit_file = builtin_dir / "stopwords_hit.txt"
        hit_file.write_text("的\n是\n在\n", encoding="utf-8")

        manager.builtin_stopwords_dir = builtin_dir

        stopwords = await manager.load_stopwords(source="hit")

        assert "的" in stopwords
        assert "是" in stopwords
        assert "在" in stopwords
        assert len(stopwords) >= 3

    @pytest.mark.asyncio
    async def test_load_stopwords_with_comments_and_empty_lines(self, tmp_path):
        """测试加载时跳过注释和空行"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        hit_file = builtin_dir / "stopwords_hit.txt"
        hit_file.write_text(
            "的\n# 这是注释\n\n是\n  \n# 另一个注释\n在\n",
            encoding="utf-8"
        )

        manager.builtin_stopwords_dir = builtin_dir

        stopwords = await manager.load_stopwords(source="hit")

        assert "的" in stopwords
        assert "是" in stopwords
        assert "在" in stopwords
        assert "# 这是注释" not in stopwords
        assert "" not in stopwords

    @pytest.mark.asyncio
    async def test_load_stopwords_fallback_when_file_not_exists(self, tmp_path):
        """测试文件不存在时使用后备停用词"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        # builtin_dir 指向不存在的目录
        manager.builtin_stopwords_dir = tmp_path / "nonexistent"

        stopwords = await manager.load_stopwords(source="hit")

        # 应该使用后备停用词
        assert len(stopwords) > 0

    @pytest.mark.asyncio
    async def test_load_custom_stopwords_file(self, tmp_path):
        """测试加载自定义停用词文件"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        custom_file = tmp_path / "my_stopwords.txt"
        custom_file.write_text("自定义1\n自定义2\n", encoding="utf-8")

        stopwords = await manager.load_stopwords(source=str(custom_file))

        assert "自定义1" in stopwords
        assert "自定义2" in stopwords

    @pytest.mark.asyncio
    async def test_load_custom_file_not_exists(self, tmp_path):
        """测试自定义文件不存在时使用后备"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        stopwords = await manager.load_stopwords(source="/nonexistent/file.txt")

        # 应该使用后备停用词
        assert len(stopwords) > 0

    @pytest.mark.asyncio
    async def test_load_with_custom_words(self, tmp_path):
        """测试添加自定义停用词列表"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        hit_file = builtin_dir / "stopwords_hit.txt"
        hit_file.write_text("的\n是\n", encoding="utf-8")
        manager.builtin_stopwords_dir = builtin_dir

        custom_words = ["额外1", "额外2", "额外3"]
        stopwords = await manager.load_stopwords(
            source="hit",
            custom_words=custom_words
        )

        assert "的" in stopwords
        assert "是" in stopwords
        assert "额外1" in stopwords
        assert "额外2" in stopwords
        assert "额外3" in stopwords
        assert len(manager.custom_stopwords) == 3


class TestCustomStopwordsManagement:
    """测试自定义停用词管理"""

    def test_add_custom_stopwords(self, stopwords_manager):
        """测试添加自定义停用词"""
        stopwords_manager.add_custom_stopwords(["词1", "词2", "词3"])

        assert "词1" in stopwords_manager.stopwords
        assert "词2" in stopwords_manager.stopwords
        assert "词3" in stopwords_manager.stopwords
        assert len(stopwords_manager.custom_stopwords) == 3

    def test_add_empty_custom_stopwords(self, stopwords_manager):
        """测试添加空列表"""
        initial_count = len(stopwords_manager.stopwords)

        stopwords_manager.add_custom_stopwords([])

        assert len(stopwords_manager.stopwords) == initial_count

    def test_add_duplicate_custom_stopwords(self, stopwords_manager):
        """测试添加重复的停用词"""
        stopwords_manager.add_custom_stopwords(["词1", "词2"])
        stopwords_manager.add_custom_stopwords(["词2", "词3"])

        # Set自动去重
        assert len(stopwords_manager.custom_stopwords) == 3

    def test_remove_stopwords(self, stopwords_manager):
        """测试移除停用词"""
        stopwords_manager.add_custom_stopwords(["词1", "词2", "词3"])

        stopwords_manager.remove_stopwords(["词2"])

        assert "词1" in stopwords_manager.stopwords
        assert "词2" not in stopwords_manager.stopwords
        assert "词3" in stopwords_manager.stopwords

    def test_remove_nonexistent_stopwords(self, stopwords_manager):
        """测试移除不存在的停用词"""
        stopwords_manager.add_custom_stopwords(["词1", "词2"])

        # 不应该抛出异常
        stopwords_manager.remove_stopwords(["不存在的词"])

        assert "词1" in stopwords_manager.stopwords
        assert "词2" in stopwords_manager.stopwords

    def test_remove_empty_list(self, stopwords_manager):
        """测试移除空列表"""
        stopwords_manager.add_custom_stopwords(["词1", "词2"])
        initial_count = len(stopwords_manager.stopwords)

        stopwords_manager.remove_stopwords([])

        assert len(stopwords_manager.stopwords) == initial_count


class TestStopwordOperations:
    """测试停用词操作"""

    def test_is_stopword(self, stopwords_manager):
        """测试检查是否为停用词"""
        stopwords_manager.add_custom_stopwords(["的", "是", "在"])

        assert stopwords_manager.is_stopword("的") is True
        assert stopwords_manager.is_stopword("是") is True
        assert stopwords_manager.is_stopword("不是停用词") is False

    def test_filter_stopwords(self, stopwords_manager):
        """测试过滤停用词"""
        stopwords_manager.add_custom_stopwords(["的", "是", "在"])

        tokens = ["今天", "的", "天气", "是", "很", "好", "的"]
        filtered = stopwords_manager.filter_stopwords(tokens)

        assert filtered == ["今天", "天气", "很", "好"]
        assert "的" not in filtered
        assert "是" not in filtered

    def test_filter_empty_tokens(self, stopwords_manager):
        """测试过滤空列表"""
        stopwords_manager.add_custom_stopwords(["的", "是"])

        filtered = stopwords_manager.filter_stopwords([])

        assert filtered == []

    def test_filter_all_stopwords(self, stopwords_manager):
        """测试全是停用词的情况"""
        stopwords_manager.add_custom_stopwords(["的", "是", "在"])

        tokens = ["的", "是", "在"]
        filtered = stopwords_manager.filter_stopwords(tokens)

        assert filtered == []


class TestSaveCustomStopwords:
    """测试保存自定义停用词"""

    @pytest.mark.asyncio
    async def test_save_custom_stopwords_default_path(self, tmp_path):
        """测试保存到默认路径"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))
        manager.add_custom_stopwords(["词1", "词2", "词3"])

        await manager.save_custom_stopwords()

        saved_file = tmp_path / "custom_stopwords.txt"
        assert saved_file.exists()

        content = saved_file.read_text(encoding="utf-8")
        assert "词1" in content
        assert "词2" in content
        assert "词3" in content

    @pytest.mark.asyncio
    async def test_save_custom_stopwords_custom_path(self, tmp_path):
        """测试保存到自定义路径"""
        manager = StopwordsManager()
        manager.add_custom_stopwords(["词A", "词B"])

        custom_file = tmp_path / "my_custom.txt"
        await manager.save_custom_stopwords(filepath=custom_file)

        assert custom_file.exists()
        content = custom_file.read_text(encoding="utf-8")
        assert "词A" in content
        assert "词B" in content

    @pytest.mark.asyncio
    async def test_save_without_custom_dir_warns(self):
        """测试没有自定义目录时警告"""
        manager = StopwordsManager()  # 没有自定义目录
        manager.add_custom_stopwords(["词1"])

        # 不应该抛出异常，但会警告
        await manager.save_custom_stopwords()

    @pytest.mark.asyncio
    async def test_save_sorted_stopwords(self, tmp_path):
        """测试保存的停用词是排序的"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))
        manager.add_custom_stopwords(["Z词", "A词", "M词"])

        await manager.save_custom_stopwords()

        saved_file = tmp_path / "custom_stopwords.txt"
        lines = saved_file.read_text(encoding="utf-8").strip().split("\n")

        assert lines == ["A词", "M词", "Z词"]


class TestGetStopwords:
    """测试获取停用词文件路径"""

    @pytest.mark.asyncio
    async def test_get_stopwords_returns_builtin_path(self, tmp_path):
        """测试返回内置文件路径"""
        manager = StopwordsManager(stopwords_dir=str(tmp_path))

        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        hit_file = builtin_dir / "stopwords_hit.txt"
        hit_file.write_text("的\n是\n", encoding="utf-8")

        manager.builtin_stopwords_dir = builtin_dir

        path = await manager.get_stopwords(source="hit")

        assert path == str(hit_file)

    @pytest.mark.asyncio
    async def test_get_stopwords_creates_fallback(self, tmp_path):
        """测试内置文件不存在时创建后备文件"""
        custom_dir = tmp_path / "custom"
        manager = StopwordsManager(stopwords_dir=str(custom_dir))

        # builtin_dir 指向不存在的位置
        manager.builtin_stopwords_dir = tmp_path / "nonexistent"

        path = await manager.get_stopwords(source="hit")

        assert path is not None
        fallback_file = Path(path)
        assert fallback_file.exists()
        assert "stopwords_hit.txt" in str(fallback_file)

    @pytest.mark.asyncio
    async def test_get_stopwords_without_custom_dir(self, tmp_path):
        """测试没有自定义目录且内置文件不存在时返回None"""
        manager = StopwordsManager()  # 没有自定义目录
        manager.builtin_stopwords_dir = tmp_path / "nonexistent"

        path = await manager.get_stopwords(source="hit")

        assert path is None


class TestWriteFallbackStopwords:
    """测试生成后备停用词文件"""

    @pytest.mark.asyncio
    async def test_write_fallback_creates_file(self, tmp_path):
        """测试创建后备文件"""
        manager = StopwordsManager()

        fallback_path = tmp_path / "fallback.txt"
        await manager._write_fallback_stopwords(fallback_path)

        assert fallback_path.exists()
        content = fallback_path.read_text(encoding="utf-8")
        assert "# Generated fallback stopwords" in content

    @pytest.mark.asyncio
    async def test_write_fallback_skips_if_exists(self, tmp_path):
        """测试文件已存在时跳过"""
        manager = StopwordsManager()

        fallback_path = tmp_path / "existing.txt"
        fallback_path.write_text("existing content", encoding="utf-8")

        await manager._write_fallback_stopwords(fallback_path)

        # 内容不应该改变
        content = fallback_path.read_text(encoding="utf-8")
        assert content == "existing content"

    @pytest.mark.asyncio
    async def test_write_fallback_creates_parent_dirs(self, tmp_path):
        """测试创建父目录"""
        manager = StopwordsManager()

        fallback_path = tmp_path / "nested" / "dir" / "fallback.txt"
        await manager._write_fallback_stopwords(fallback_path)

        assert fallback_path.exists()
        assert fallback_path.parent.exists()


class TestGetStopwordsManagerSingleton:
    """测试全局单例"""

    def test_singleton_returns_same_instance(self):
        """测试返回相同的实例"""
        manager1 = get_stopwords_manager()
        manager2 = get_stopwords_manager()

        assert manager1 is manager2

    def test_singleton_persists_state(self):
        """测试单例保持状态"""
        manager1 = get_stopwords_manager()
        manager1.add_custom_stopwords(["测试词"])

        manager2 = get_stopwords_manager()

        assert "测试词" in manager2.stopwords


class TestLoadFromFileErrorHandling:
    """测试文件加载错误处理"""

    @pytest.mark.asyncio
    async def test_load_from_file_handles_decode_error(self, tmp_path):
        """测试处理文件编码错误"""
        manager = StopwordsManager()

        bad_file = tmp_path / "bad_encoding.txt"
        bad_file.write_bytes(b"\xff\xfe\xfd")  # 无效的UTF-8

        # 应该返回空set而不是崩溃
        stopwords = await manager._load_from_file(bad_file)

        assert stopwords == set()
