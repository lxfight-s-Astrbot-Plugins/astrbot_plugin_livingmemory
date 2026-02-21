"""
Tests for TextProcessor behaviors.
"""

from pathlib import Path

import pytest
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor


def test_tokenize_handles_empty_and_basic_cleaning():
    processor = TextProcessor()
    assert processor.tokenize("") == []
    assert processor.tokenize("   ") == []

    tokens = processor.tokenize("Visit https://example.com now!!!")
    # URL/punctuation should be cleaned; keep meaningful tokens.
    assert "Visit" in tokens or "visit" in [t.lower() for t in tokens]


def test_tokenize_removes_common_stopwords():
    processor = TextProcessor()
    tokens = processor.tokenize("我 今天 去 图书馆", remove_stopwords=True)
    # "我" is stopword, but "今天"/"图书馆" should remain.
    assert "我" not in tokens
    assert any(t in tokens for t in ["今天", "图书馆"])


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
