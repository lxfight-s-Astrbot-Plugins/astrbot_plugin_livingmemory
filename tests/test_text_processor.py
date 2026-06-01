"""
Tests for TextProcessor behaviors.
"""

from pathlib import Path

import astrbot_plugin_livingmemory.core.processors.text_processor as text_processor_mod
import pytest
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.utils.stopwords_manager import StopwordsManager


def test_tokenize_handles_empty_and_basic_cleaning():
    processor = TextProcessor()
    assert processor.tokenize("") == []
    assert processor.tokenize("   ") == []

    tokens = processor.tokenize("Visit https://example.com now!!!")
    # URL/punctuation should be cleaned; keep meaningful tokens.
    assert "Visit" in tokens or "visit" in [t.lower() for t in tokens]


def test_tokenize_removes_common_stopwords():
    processor = TextProcessor()
    # 先加载停用词，否则 remove_stopwords 无效果
    processor.add_stopwords(["我"])

    tokens = processor.tokenize("我 今天 去 图书馆", remove_stopwords=True)
    # "我" 是停用词，应被移除
    assert "我" not in tokens
    assert len(tokens) >= 1


@pytest.mark.asyncio
async def test_load_stopwords_and_custom_words(tmp_path: Path):
    processor = TextProcessor()
    path = tmp_path / "stopwords.txt"
    path.write_text("# comment\nalpha\nbeta\n", encoding="utf-8")

    loaded = await processor.load_stopwords(str(path))
    assert "alpha" in loaded
    assert processor.is_stopword("alpha")

    processor.add_stopwords(["gamma"])
    assert processor.is_stopword("gamma")
    processor.remove_stopwords_from_list(["gamma"])
    assert not processor.is_stopword("gamma")


def test_preprocess_for_bm25_and_word_freq():
    processor = TextProcessor()
    processed = processor.preprocess_for_bm25("编程 很 有趣，编程 真 快乐")
    assert isinstance(processed, str)
    assert len(processed) > 0

    freq = processor.get_word_freq(["我 爱 编程", "编程 很 有趣"])
    assert isinstance(freq, dict)
    assert len(freq) > 0


def test_tokenize_falls_back_when_jieba_runtime_fails(monkeypatch):
    class BrokenJieba:
        @staticmethod
        def cut_for_search(text):
            raise AttributeError(
                "module 'pkg_resources' has no attribute 'resource_stream'"
            )

    monkeypatch.setattr(text_processor_mod, "JIEBA_AVAILABLE", True)
    monkeypatch.setattr(text_processor_mod, "JIEBA_RUNTIME_DISABLED", False)
    monkeypatch.setattr(text_processor_mod, "jieba", BrokenJieba)

    processor = TextProcessor()
    with pytest.warns(UserWarning, match="jieba 分词初始化失败"):
        tokens = processor.tokenize("编程快乐")

    assert tokens
    assert "编" in tokens
    assert text_processor_mod.JIEBA_RUNTIME_DISABLED is True


@pytest.mark.asyncio
async def test_stopwords_manager_materializes_fallback_when_builtin_missing(
    tmp_path: Path,
):
    manager = StopwordsManager(str(tmp_path))
    manager.builtin_stopwords_dir = tmp_path / "missing"

    stopwords_path = await manager.get_stopwords()
    loaded = await manager.load_stopwords()

    assert stopwords_path is not None
    assert Path(stopwords_path).exists()
    assert "的" in loaded


@pytest.mark.asyncio
async def test_text_processor_async_init_uses_stopwords_fallback(tmp_path: Path):
    processor = TextProcessor(str(tmp_path))

    await processor.async_init()

    assert processor.is_stopword("的")
    assert (tmp_path / "stopwords_hit.txt").exists()
