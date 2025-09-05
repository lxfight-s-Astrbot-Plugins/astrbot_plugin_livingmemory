# -*- coding: utf-8 -*-
"""
test_base_handler.py - 基础处理器测试
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from core.handlers.base_handler import TestableBaseHandler
from tests.conftest import TEST_CONFIG


class TestBaseHandler:
    """基础处理器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.mock_context = Mock()
        self.handler = TestableBaseHandler(self.mock_context, TEST_CONFIG)
    
    def test_init(self):
        """测试初始化"""
        assert self.handler.context == self.mock_context
        assert self.handler.config == TEST_CONFIG
    
    def test_create_response_success(self):
        """测试创建成功响应"""
        response = self.handler.create_response(True, "成功消息", {"data": "test"})
        
        assert response["success"] is True
        assert response["message"] == "成功消息"
        assert response["data"] == {"data": "test"}
    
    def test_create_response_failure(self):
        """测试创建失败响应"""
        response = self.handler.create_response(False, "失败消息")
        
        assert response["success"] is False
        assert response["message"] == "失败消息"
        assert response["data"] is None
    
    def test_safe_parse_metadata_dict(self):
        """测试解析字典类型元数据"""
        metadata = {"key": "value", "number": 123}
        result = self.handler.safe_parse_metadata(metadata)
        
        assert result == metadata
    
    def test_safe_parse_metadata_json_string(self):
        """测试解析JSON字符串元数据"""
        metadata_str = '{"key": "value", "number": 123}'
        result = self.handler.safe_parse_metadata(metadata_str)
        
        assert result == {"key": "value", "number": 123}
    
    def test_safe_parse_metadata_invalid_json(self):
        """测试解析无效JSON字符串"""
        invalid_json = "{invalid json}"
        result = self.handler.safe_parse_metadata(invalid_json)
        
        assert result == {}
    
    def test_safe_parse_metadata_none(self):
        """测试解析None值"""
        result = self.handler.safe_parse_metadata(None)
        
        assert result == {}
    
    def test_format_timestamp_valid(self):
        """测试格式化有效时间戳"""
        timestamp = 1609459200.0  # 2021-01-01 00:00:00 UTC
        result = self.handler.format_timestamp(timestamp)
        
        assert "2021-01-01" in result
        # 由于时区转换，时间可能不是00:00:00
        assert len(result) > 10
    
    def test_format_timestamp_none(self):
        """测试格式化None时间戳"""
        result = self.handler.format_timestamp(None)
        
        assert result == "未知"
    
    def test_format_timestamp_invalid(self):
        """测试格式化无效时间戳"""
        result = self.handler.format_timestamp("invalid")
        
        assert result == "未知"
    
    def test_get_timezone(self):
        """测试获取时区"""
        with patch('pytz.timezone') as mock_timezone:
            mock_tz = Mock()
            mock_timezone.return_value = mock_tz
            
            result = self.handler.get_timezone()
            
            assert result == mock_tz
            mock_timezone.assert_called_once_with("Asia/Shanghai")
    
    def test_format_memory_card(self):
        """测试格式化记忆卡片"""
        # 创建模拟的记忆结果
        mock_result = Mock()
        mock_result.data = {
            "id": 1,
            "text": "测试记忆内容",
            "metadata": json.dumps({
                "create_time": 1609459200.0,
                "last_access_time": 1609459200.0,
                "importance": 0.8,
                "event_type": "FACT"
            })
        }
        mock_result.similarity = 0.95
        
        result = self.handler.format_memory_card(mock_result)
        
        assert "ID: 1" in result
        assert "记 忆 度: 0.95" in result
        assert "重 要 性: 0.80" in result
        assert "记忆类型: FACT" in result
        assert "测试记忆内容" in result