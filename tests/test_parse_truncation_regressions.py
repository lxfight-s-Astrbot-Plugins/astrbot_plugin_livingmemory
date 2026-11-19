"""Regression tests: truncated regex fallback must not become a normal memory (#446).

When the model emits unescaped double quotes, whole-JSON parsing and repair both
fail. The old regex fallback then truncated the summary at the first inner quote
and turned punctuation into key facts, while the quality gate still said
"normal". The fallback must fail instead of fabricating a partial memory.
"""

import pytest

from astrbot_plugin_livingmemory.core.processors.memory_processor_parse import (
    MemoryParseError,
    extract_by_regex,
    parse_llm_response,
    validate_summary_quality,
)

_UNESCAPED_QUOTES = (
    '{"summary":"小明今天晚上聊到周末的阅读安排，他选了"海边的书"，准备周末读。",'
    '"topics":["选书"],'
    '"key_facts":["小明选了"海边的书"","周末准备阅读"],'
    '"sentiment":"neutral","importance":0.5}'
)


def test_unrecoverable_json_raises_instead_of_returning_partial_summary() -> None:
    with pytest.raises(MemoryParseError):
        parse_llm_response(_UNESCAPED_QUOTES, False)


def test_extract_by_regex_returns_none_when_no_block_parses() -> None:
    assert extract_by_regex(_UNESCAPED_QUOTES, False) is None
    # 字段名出现但整体不是合法 JSON，同样不再逐字段拼接。
    assert extract_by_regex('"summary": "只有字段，没有外层对象"', False) is None


def test_extract_by_regex_still_finds_embedded_valid_json() -> None:
    text = (
        "好的，总结如下：\n"
        '{"summary": "张三确认了周五的会议安排", "topics": ["会议"], '
        '"key_facts": ["周五开会"], "sentiment": "neutral", "importance": 0.6}\n'
        "以上。"
    )

    data = extract_by_regex(text, False)

    assert data is not None
    assert data["summary"] == "张三确认了周五的会议安排"


def test_quality_gate_rejects_punctuation_fragment_facts() -> None:
    structured = {
        "summary": "小明今天晚上聊到周末的阅读安排，他选了",
        "key_facts": ["小明选了", ","],
        "importance": 0.5,
    }

    assert validate_summary_quality(structured) == "low"


def test_quality_gate_keeps_normal_facts() -> None:
    structured = {
        "summary": "小明确认周末会去海边读《海边的书》。",
        "key_facts": ["小明周末读书", "选了海边的书"],
        "importance": 0.6,
    }

    assert validate_summary_quality(structured) == "normal"
