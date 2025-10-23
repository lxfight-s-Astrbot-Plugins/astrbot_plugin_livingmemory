# -*- coding: utf-8 -*-
"""
test_validators.py - 测试命令参数验证器
"""

import pytest
from core.commands.validators import CommandValidator, validate_params
from unittest.mock import Mock


class TestCommandValidator:
    """测试 CommandValidator 类"""

    def test_validate_memory_id_valid_int(self):
        """测试有效的整数 ID"""
        is_valid, error = CommandValidator.validate_memory_id(123)
        assert is_valid is True
        assert error is None

    def test_validate_memory_id_valid_string(self):
        """测试有效的字符串 ID"""
        is_valid, error = CommandValidator.validate_memory_id("123")
        assert is_valid is True
        assert error is None

    def test_validate_memory_id_invalid_string(self):
        """测试无效的字符串 ID"""
        is_valid, error = CommandValidator.validate_memory_id("abc")
        assert is_valid is False
        assert "有效的整数" in error

    def test_validate_memory_id_empty_string(self):
        """测试空字符串 ID"""
        is_valid, error = CommandValidator.validate_memory_id("")
        assert is_valid is False
        assert "不能为空" in error

    def test_validate_content_length_valid(self):
        """测试有效的内容长度"""
        content = "这是一段测试内容"
        is_valid, error = CommandValidator.validate_content_length(content)
        assert is_valid is True
        assert error is None

    def test_validate_content_length_too_long(self):
        """测试过长的内容"""
        content = "a" * (CommandValidator.MAX_CONTENT_LENGTH + 1)
        is_valid, error = CommandValidator.validate_content_length(content)
        assert is_valid is False
        assert "长度不能超过" in error

    def test_validate_search_count_valid(self):
        """测试有效的搜索数量"""
        is_valid, error = CommandValidator.validate_search_count(5)
        assert is_valid is True
        assert error is None

    def test_validate_search_count_too_large(self):
        """测试过大的搜索数量"""
        is_valid, error = CommandValidator.validate_search_count(100)
        assert is_valid is False
        assert "不能超过" in error

    def test_validate_search_count_too_small(self):
        """测试过小的搜索数量"""
        is_valid, error = CommandValidator.validate_search_count(0)
        assert is_valid is False
        assert "必须大于 0" in error

    def test_validate_query_length_valid(self):
        """测试有效的查询长度"""
        is_valid, error = CommandValidator.validate_query_length("测试查询")
        assert is_valid is True
        assert error is None

    def test_validate_query_length_empty(self):
        """测试空查询"""
        is_valid, error = CommandValidator.validate_query_length("")
        assert is_valid is False
        assert "不能为空" in error

    def test_validate_query_length_too_long(self):
        """测试过长的查询"""
        query = "a" * (CommandValidator.MAX_QUERY_LENGTH + 1)
        is_valid, error = CommandValidator.validate_query_length(query)
        assert is_valid is False
        assert "长度不能超过" in error

    def test_validate_importance_valid(self):
        """测试有效的重要性分数"""
        is_valid, error = CommandValidator.validate_importance(0.5)
        assert is_valid is True
        assert error is None

    def test_validate_importance_boundary_lower(self):
        """测试重要性分数下界"""
        is_valid, error = CommandValidator.validate_importance(0.0)
        assert is_valid is True
        assert error is None

    def test_validate_importance_boundary_upper(self):
        """测试重要性分数上界"""
        is_valid, error = CommandValidator.validate_importance(1.0)
        assert is_valid is True
        assert error is None

    def test_validate_importance_too_low(self):
        """测试过低的重要性分数"""
        is_valid, error = CommandValidator.validate_importance(-0.1)
        assert is_valid is False
        assert "0.0 到 1.0 之间" in error

    def test_validate_importance_too_high(self):
        """测试过高的重要性分数"""
        is_valid, error = CommandValidator.validate_importance(1.1)
        assert is_valid is False
        assert "0.0 到 1.0 之间" in error

    def test_validate_field_name_valid(self):
        """测试有效的字段名"""
        allowed = ["content", "importance", "type"]
        is_valid, error = CommandValidator.validate_field_name("content", allowed)
        assert is_valid is True
        assert error is None

    def test_validate_field_name_invalid(self):
        """测试无效的字段名"""
        allowed = ["content", "importance", "type"]
        is_valid, error = CommandValidator.validate_field_name("invalid", allowed)
        assert is_valid is False
        assert "无效的字段名" in error

    def test_validate_fusion_strategy_valid(self):
        """测试有效的融合策略"""
        allowed = ["rrf", "weighted", "hybrid_rrf"]
        is_valid, error = CommandValidator.validate_fusion_strategy("rrf", allowed)
        assert is_valid is True
        assert error is None

    def test_validate_fusion_strategy_invalid(self):
        """测试无效的融合策略"""
        allowed = ["rrf", "weighted", "hybrid_rrf"]
        is_valid, error = CommandValidator.validate_fusion_strategy("invalid", allowed)
        assert is_valid is False
        assert "无效的策略" in error

    def test_validate_search_mode_valid(self):
        """测试有效的检索模式"""
        is_valid, error = CommandValidator.validate_search_mode("hybrid")
        assert is_valid is True
        assert error is None

    def test_validate_search_mode_invalid(self):
        """测试无效的检索模式"""
        is_valid, error = CommandValidator.validate_search_mode("invalid")
        assert is_valid is False
        assert "无效的模式" in error

    def test_validate_fusion_weight_valid(self):
        """测试有效的融合权重"""
        is_valid, error = CommandValidator.validate_fusion_weight(0.7)
        assert is_valid is True
        assert error is None

    def test_validate_fusion_weight_boundary(self):
        """测试融合权重边界"""
        is_valid, error = CommandValidator.validate_fusion_weight(0.0)
        assert is_valid is True

        is_valid, error = CommandValidator.validate_fusion_weight(1.0)
        assert is_valid is True

    def test_validate_fusion_weight_invalid(self):
        """测试无效的融合权重"""
        is_valid, error = CommandValidator.validate_fusion_weight(1.5)
        assert is_valid is False
        assert "0.0 到 1.0 之间" in error


@pytest.mark.asyncio
class TestValidateParamsDecorator:
    """测试 validate_params 装饰器"""

    async def test_valid_params(self):
        """测试有效的参数"""
        class MockEvent:
            def __init__(self):
                self.results = []

            def plain_result(self, text):
                self.results.append(text)
                return text

        plugin = Mock()
        event = MockEvent()

        @validate_params(
            k=CommandValidator.validate_search_count
        )
        async def test_command(self, event, k: int = 3):
            yield event.plain_result(f"搜索数量: {k}")

        # 执行命令
        results = []
        async for result in test_command(plugin, event, k=5):
            results.append(result)

        # 验证命令正常执行
        assert len(results) == 1
        assert "搜索数量: 5" in results[0]

    async def test_invalid_params(self):
        """测试无效的参数"""
        class MockEvent:
            def __init__(self):
                self.results = []

            def plain_result(self, text):
                self.results.append(text)
                return text

        plugin = Mock()
        event = MockEvent()

        @validate_params(
            k=CommandValidator.validate_search_count
        )
        async def test_command(self, event, k: int = 3):
            yield event.plain_result(f"搜索数量: {k}")

        # 执行命令，传入无效参数
        results = []
        async for result in test_command(plugin, event, k=100):
            results.append(result)

        # 验证返回了错误消息
        assert len(results) == 1
        assert "参数验证失败" in results[0]
        assert "不能超过" in results[0]
