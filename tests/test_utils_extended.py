"""Extended tests for core/utils/__init__.py to improve coverage."""

import asyncio
import json
import time
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytz
from astrbot_plugin_livingmemory.core.utils import (
    extract_json_from_response,
    format_memories_for_injection,
    get_now_datetime,
    retry_on_failure,
    safe_parse_metadata,
    safe_serialize_metadata,
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


class TestSafeSerializeMetadata:
    """测试元数据序列化"""

    def test_serialize_simple_dict(self):
        """测试简单字典序列化"""
        data = {"key": "value", "number": 42}
        result = safe_serialize_metadata(data)
        assert json.loads(result) == data

    def test_serialize_with_unicode(self):
        """测试Unicode字符序列化"""
        data = {"中文": "测试", "emoji": "😀"}
        result = safe_serialize_metadata(data)
        # ensure_ascii=False 应该保留Unicode字符
        assert "中文" in result
        assert "测试" in result

    def test_serialize_nested_dict(self):
        """测试嵌套字典序列化"""
        data = {"outer": {"inner": {"deep": "value"}}}
        result = safe_serialize_metadata(data)
        assert json.loads(result) == data

    def test_serialize_empty_dict(self):
        """测试空字典序列化"""
        result = safe_serialize_metadata({})
        assert result == "{}"


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


class TestRetryOnFailure:
    """测试重试机制"""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_first_attempt(self):
        """测试第一次尝试成功"""
        async_func = AsyncMock(return_value="success")

        result = await retry_on_failure(async_func, max_retries=3)

        assert result == "success"
        async_func.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self):
        """测试重试后成功"""
        call_count = 0

        async def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Still failing")
            return "success"

        result = await retry_on_failure(
            failing_func,
            max_retries=3,
            backoff_factor=0.01,  # 快速重试
            exceptions=(ValueError,),
        )

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_exception(self):
        """测试重试耗尽后抛出异常"""
        async_func = AsyncMock(side_effect=ValueError("Always fails"))

        with pytest.raises(ValueError, match="Always fails"):
            await retry_on_failure(
                async_func,
                max_retries=2,
                backoff_factor=0.01,
                exceptions=(ValueError,),
            )

        assert async_func.await_count == 3  # 初始 + 2次重试

    @pytest.mark.asyncio
    async def test_retry_with_sync_function(self):
        """测试同步函数重试"""
        call_count = 0

        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("First try fails")
            return "sync success"

        result = await retry_on_failure(
            sync_func,
            max_retries=2,
            backoff_factor=0.01,
            exceptions=(ValueError,),
        )

        assert result == "sync success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_respects_exception_types(self):
        """测试只重试指定的异常类型"""
        async_func = AsyncMock(side_effect=RuntimeError("Wrong exception"))

        # 配置只重试 ValueError
        with pytest.raises(RuntimeError, match="Wrong exception"):
            await retry_on_failure(
                async_func,
                max_retries=2,
                backoff_factor=0.01,
                exceptions=(ValueError,),
            )

        # 应该在第一次就失败，没有重试
        assert async_func.await_count == 1


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


class TestGetNowDatetime:
    """测试获取当前时间"""

    def test_get_now_datetime_default_timezone(self):
        """测试默认时区（Asia/Shanghai）"""
        result = get_now_datetime()

        assert isinstance(result, datetime)
        assert result.tzinfo is not None
        assert result.tzinfo.zone == "Asia/Shanghai"

    def test_get_now_datetime_custom_timezone(self):
        """测试自定义时区"""
        result = get_now_datetime(tz_str="America/New_York")

        assert isinstance(result, datetime)
        assert result.tzinfo.zone == "America/New_York"

    def test_get_now_datetime_utc(self):
        """测试UTC时区"""
        result = get_now_datetime(tz_str="UTC")

        assert isinstance(result, datetime)
        assert result.tzinfo.zone == "UTC"

    def test_get_now_datetime_returns_current_time(self):
        """测试返回的是当前时间"""
        before = datetime.now(pytz.timezone("Asia/Shanghai"))
        result = get_now_datetime()
        after = datetime.now(pytz.timezone("Asia/Shanghai"))

        # 时间应该在调用前后之间
        assert before <= result <= after


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
