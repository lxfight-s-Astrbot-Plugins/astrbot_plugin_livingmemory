"""Extended tests for core/utils/__init__.py to improve coverage."""

import json
import time
from datetime import datetime

import pytz
from astrbot_plugin_livingmemory.core.utils import (
    extract_json_from_response,
    format_memories_for_injection,
    safe_parse_metadata,
    validate_timestamp,
)


class TestSafeParseMetadata:
    """测试元数据解析"""

    def test_parse_dict_returns_as_is(self):
        """测试字典直接返回"""
        data = {"key": "value", "nested": {"a": 1}}
        result = safe_parse_metadata(data)
        assert result == data

    def test_parse_valid_json_string(self):
        """测试有效的JSON字符串"""
        json_str = '{"key": "value", "number": 42}'
        result = safe_parse_metadata(json_str)
        assert result == {"key": "value", "number": 42}

    def test_parse_invalid_json_returns_empty_dict(self):
        """测试无效JSON返回空字典"""
        invalid_json = '{"key": invalid}'
        result = safe_parse_metadata(invalid_json)
        assert result == {}

    def test_parse_non_dict_non_string_returns_empty_dict(self):
        """测试非字典非字符串类型返回空字典"""
        result1 = safe_parse_metadata(123)
        result2 = safe_parse_metadata([1, 2, 3])
        result3 = safe_parse_metadata(None)

        assert result1 == {}
        assert result2 == {}
        assert result3 == {}

    def test_parse_empty_string_returns_empty_dict(self):
        """测试空字符串返回空字典"""
        result = safe_parse_metadata("")
        assert result == {}


class TestValidateTimestamp:
    """测试时间戳验证"""

    def test_validate_int_timestamp(self):
        """测试整数时间戳"""
        timestamp = 1609459200  # 2021-01-01 00:00:00 UTC
        result = validate_timestamp(timestamp)
        assert result == 1609459200.0

    def test_validate_float_timestamp(self):
        """测试浮点时间戳"""
        timestamp = 1609459200.5
        result = validate_timestamp(timestamp)
        assert result == 1609459200.5

    def test_validate_string_timestamp(self):
        """测试字符串时间戳"""
        result = validate_timestamp("1609459200")
        assert result == 1609459200.0

    def test_validate_invalid_string_uses_default(self):
        """测试无效字符串使用默认值"""
        default = 1234567890.0
        result = validate_timestamp("not a number", default_time=default)
        assert result == default

    def test_validate_datetime_object(self):
        """测试datetime对象"""
        dt = datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
        result = validate_timestamp(dt)
        assert result == dt.timestamp()

    def test_validate_unsupported_type_uses_default(self):
        """测试不支持的类型使用默认值"""
        default = 1234567890.0
        result = validate_timestamp([1, 2, 3], default_time=default)
        assert result == default

    def test_validate_none_uses_current_time(self):
        """测试None使用当前时间"""
        before = time.time()
        result = validate_timestamp(None)
        after = time.time()

        assert before <= result <= after


class TestExtractJsonFromResponse:
    """测试从响应中提取JSON"""

    def test_extract_json_from_plain_json(self):
        """测试提取纯JSON"""
        text = '{"key": "value", "number": 42}'
        result = extract_json_from_response(text)
        assert result == text

    def test_extract_json_from_markdown_code_block(self):
        """测试从Markdown代码块提取JSON"""
        text = """
        Here is the JSON:
        ```json
        {"key": "value"}
        ```
        """
        result = extract_json_from_response(text)
        assert json.loads(result) == {"key": "value"}

    def test_extract_json_from_generic_code_block(self):
        """测试从通用代码块提取JSON"""
        text = """
        ```
        {"extracted": true}
        ```
        """
        result = extract_json_from_response(text)
        assert json.loads(result) == {"extracted": True}

    def test_extract_returns_original_if_no_code_block(self):
        """测试无代码块时返回原文"""
        text = "Just plain text"
        result = extract_json_from_response(text)
        assert result == text

    def test_extract_handles_multiple_code_blocks(self):
        """测试处理多个代码块（取第一个）"""
        text = """
        First block:
        ```json
        {"first": true}
        ```
        Second block:
        ```json
        {"second": true}
        ```
        """
        result = extract_json_from_response(text)
        assert json.loads(result) == {"first": True}


class TestFormatMemoriesForInjection:
    """测试格式化记忆注入"""

    def test_format_empty_list(self):
        """测试空记忆列表"""
        result = format_memories_for_injection([])
        assert result == ""

    def test_format_single_memory(self):
        """测试单条记忆"""
        memories = [
            {
                "content": "用户喜欢吃披萨",
                "importance": 0.8,
                "created_at": 1609459200.0,
            }
        ]

        result = format_memories_for_injection(memories)

        assert "用户喜欢吃披萨" in result
        assert "重要性" in result or "importance" in result.lower()

    def test_format_multiple_memories(self):
        """测试多条记忆"""
        memories = [
            {"content": "记忆1", "importance": 0.8},
            {"content": "记忆2", "importance": 0.6},
            {"content": "记忆3", "importance": 0.9},
        ]

        result = format_memories_for_injection(memories)

        assert "记忆1" in result
        assert "记忆2" in result
        assert "记忆3" in result

    def test_format_handles_missing_fields(self):
        """测试处理缺失字段"""
        memories = [
            {"content": "只有内容"},
            {"content": "有重要性", "importance": 0.7},
        ]

        # 应该不抛出异常
        result = format_memories_for_injection(memories)
        assert "只有内容" in result
        assert "有重要性" in result

    def test_format_with_metadata(self):
        """测试包含元数据的记忆"""
        memories = [
            {
                "content": "带元数据的记忆",
                "importance": 0.8,
                "metadata": {"session_id": "test_session", "persona_id": "default"},
            }
        ]

        result = format_memories_for_injection(memories)
        assert "带元数据的记忆" in result

    def test_format_uses_persona_summary_without_repeating_key_facts(self):
        memories = [
            {
                "content": "事实检索摘要 | 张三周五发布",
                "score": 0.9,
                "metadata": {
                    "persona_summary": "我记得张三周五要发布呀！",
                    "key_facts": ["张三周五发布"],
                    "importance": 0.8,
                },
            }
        ]

        result = format_memories_for_injection(memories)

        assert "我记得张三周五要发布呀！" in result
        assert "事实检索摘要" not in result
        assert result.count("张三周五发布") == 1

    def test_format_strips_exact_key_fact_suffix_from_legacy_v2_content(self):
        memories = [
            {
                "content": "我记得张三周五要发布呀！ | 张三周五发布；需要准备清单",
                "score": 0.9,
                "metadata": {
                    "summary_schema_version": "v2",
                    "key_facts": ["张三周五发布", "需要准备清单"],
                    "importance": 0.8,
                },
            }
        ]

        result = format_memories_for_injection(memories)

        assert "我记得张三周五要发布呀！" in result
        assert result.count("张三周五发布") == 1
        assert result.count("需要准备清单") == 1

    def test_format_preserves_nonmatching_legacy_content(self):
        memories = [
            {
                "content": "普通旧记忆正文",
                "metadata": {
                    "summary_schema_version": "v2",
                    "key_facts": ["另一个事实"],
                },
            }
        ]

        result = format_memories_for_injection(memories)

        assert "普通旧记忆正文" in result

    def test_format_includes_deterministic_source_time_tags(self):
        memories = [
            {
                "content": "发布计划已确认",
                "metadata": {
                    "time_tags": ["2025-05-01", "2025-05-02"],
                    "importance": 0.8,
                },
            }
        ]

        result = format_memories_for_injection(memories)

        assert "Source time: 2025-05-01 - 2025-05-02" in result


class TestNumberUtils:
    """测试数字工具函数"""

    def test_safe_parse_metadata_with_numbers(self):
        """测试解析包含各种数字的元数据"""
        data = {
            "int_val": 42,
            "float_val": 3.14,
            "negative": -10,
            "zero": 0,
        }

        json_str = json.dumps(data)
        result = safe_parse_metadata(json_str)

        assert result["int_val"] == 42
        assert result["float_val"] == 3.14
        assert result["negative"] == -10
        assert result["zero"] == 0


class TestTimestampEdgeCases:
    """测试时间戳边界情况"""

    def test_validate_very_large_timestamp(self):
        """测试非常大的时间戳"""
        # 2100年的时间戳
        future_timestamp = 4102444800
        result = validate_timestamp(future_timestamp)
        assert result == 4102444800.0

    def test_validate_zero_timestamp(self):
        """测试零时间戳"""
        result = validate_timestamp(0)
        assert result == 0.0

    def test_validate_negative_timestamp(self):
        """测试负时间戳（1970年之前）"""
        result = validate_timestamp(-86400)  # 1969-12-31
        assert result == -86400.0

    def test_datetime_without_timezone(self):
        """测试没有时区的datetime对象"""
        dt = datetime(2021, 1, 1, 0, 0, 0)  # naive datetime
        result = validate_timestamp(dt)
        # 应该能正常转换
        assert isinstance(result, float)
