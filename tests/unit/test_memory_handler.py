# -*- coding: utf-8 -*-
"""
test_memory_handler.py - 记忆管理处理器测试
"""

import pytest
import json
from unittest.mock import Mock, AsyncMock

from core.handlers.memory_handler import MemoryHandler
from tests.conftest import TEST_CONFIG


class TestMemoryHandler:
    """记忆管理处理器测试类"""

    def setup_method(self):
        """测试前设置"""
        self.mock_context = Mock()
        self.mock_faiss_manager = Mock()
        self.handler = MemoryHandler(
            self.mock_context, TEST_CONFIG, self.mock_faiss_manager
        )

    @pytest.mark.asyncio
    async def test_edit_memory_content(self):
        """测试编辑记忆内容"""
        # 模拟faiss_manager.update_memory的返回值
        mock_result = {
            "success": True,
            "message": "更新成功",
            "updated_fields": ["content"],
            "memory_id": 123,
        }
        self.mock_faiss_manager.update_memory = AsyncMock(return_value=mock_result)

        result = await self.handler.edit_memory(
            "123", "content", "新的记忆内容", "测试更新"
        )

        assert result["success"] is True
        assert "更新成功" in result["message"]

        # 验证调用参数
        self.mock_faiss_manager.update_memory.assert_called_once_with(
            memory_id=123, update_reason="测试更新", content="新的记忆内容"
        )

    @pytest.mark.asyncio
    async def test_edit_memory_importance_valid(self):
        """测试编辑记忆重要性（有效值）"""
        mock_result = {
            "success": True,
            "message": "更新成功",
            "updated_fields": ["importance"],
        }
        self.mock_faiss_manager.update_memory = AsyncMock(return_value=mock_result)

        result = await self.handler.edit_memory(
            "123", "importance", "0.9", "提高重要性"
        )

        assert result["success"] is True
        self.mock_faiss_manager.update_memory.assert_called_once_with(
            memory_id=123, update_reason="提高重要性", importance=0.9
        )

    @pytest.mark.asyncio
    async def test_edit_memory_importance_invalid_range(self):
        """测试编辑记忆重要性（无效范围）"""
        result = await self.handler.edit_memory("123", "importance", "1.5", "无效值")

        assert result["success"] is False
        assert "重要性评分必须在 0.0 到 1.0 之间" in result["message"]

        # 验证没有调用update_memory
        self.mock_faiss_manager.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_memory_importance_invalid_type(self):
        """测试编辑记忆重要性（无效类型）"""
        result = await self.handler.edit_memory(
            "123", "importance", "invalid", "非数字"
        )

        assert result["success"] is False
        assert "重要性评分必须是数字" in result["message"]

        # 验证没有调用update_memory
        self.mock_faiss_manager.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_memory_type_valid(self):
        """测试编辑记忆类型（有效值）"""
        mock_result = {
            "success": True,
            "message": "更新成功",
            "updated_fields": ["event_type"],
        }
        self.mock_faiss_manager.update_memory = AsyncMock(return_value=mock_result)

        result = await self.handler.edit_memory("123", "type", "PREFERENCE", "重新分类")

        assert result["success"] is True
        self.mock_faiss_manager.update_memory.assert_called_once_with(
            memory_id=123, update_reason="重新分类", event_type="PREFERENCE"
        )

    @pytest.mark.asyncio
    async def test_edit_memory_type_invalid(self):
        """测试编辑记忆类型（无效值）"""
        result = await self.handler.edit_memory(
            "123", "type", "INVALID_TYPE", "无效类型"
        )

        assert result["success"] is False
        assert "无效的事件类型" in result["message"]

        # 验证没有调用update_memory
        self.mock_faiss_manager.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_memory_status_valid(self):
        """测试编辑记忆状态（有效值）"""
        mock_result = {
            "success": True,
            "message": "更新成功",
            "updated_fields": ["status"],
        }
        self.mock_faiss_manager.update_memory = AsyncMock(return_value=mock_result)

        result = await self.handler.edit_memory("123", "status", "archived", "项目完成")

        assert result["success"] is True
        self.mock_faiss_manager.update_memory.assert_called_once_with(
            memory_id=123, update_reason="项目完成", status="archived"
        )

    @pytest.mark.asyncio
    async def test_edit_memory_status_invalid(self):
        """测试编辑记忆状态（无效值）"""
        result = await self.handler.edit_memory(
            "123", "status", "INVALID_STATUS", "无效状态"
        )

        assert result["success"] is False
        assert "无效的状态" in result["message"]

        # 验证没有调用update_memory
        self.mock_faiss_manager.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_memory_unknown_field(self):
        """测试编辑未知字段"""
        result = await self.handler.edit_memory(
            "123", "unknown_field", "value", "未知字段"
        )

        assert result["success"] is False
        assert "未知的字段" in result["message"]

        # 验证没有调用update_memory
        self.mock_faiss_manager.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_memory_string_id(self):
        """测试使用字符串ID编辑记忆"""
        mock_result = {
            "success": True,
            "message": "更新成功",
            "updated_fields": ["content"],
        }
        self.mock_faiss_manager.update_memory = AsyncMock(return_value=mock_result)

        result = await self.handler.edit_memory(
            "abc123", "content", "新内容", "字符串ID"
        )

        assert result["success"] is True
        self.mock_faiss_manager.update_memory.assert_called_once_with(
            memory_id="abc123", update_reason="字符串ID", content="新内容"
        )

    @pytest.mark.asyncio
    async def test_edit_memory_no_faiss_manager(self):
        """测试没有faiss_manager时的错误处理"""
        handler = MemoryHandler(self.mock_context, TEST_CONFIG, None)

        result = await handler.edit_memory("123", "content", "新内容")

        assert result["success"] is False
        assert "记忆库尚未初始化" in result["message"]

    @pytest.mark.asyncio
    async def test_edit_memory_exception(self):
        """测试编辑记忆时的异常处理"""
        self.mock_faiss_manager.update_memory = AsyncMock(
            side_effect=Exception("数据库错误")
        )

        result = await self.handler.edit_memory("123", "content", "新内容")

        assert result["success"] is False
        assert "编辑记忆时发生错误" in result["message"]

    @pytest.mark.asyncio
    async def test_get_memory_details_success(self):
        """测试获取记忆详细信息（成功）"""
        # 模拟数据库查询结果
        mock_docs = [
            {
                "id": 123,
                "content": "测试记忆内容",
                "metadata": json.dumps(
                    {
                        "create_time": 1609459200.0,
                        "last_access_time": 1609459200.0,
                        "importance": 0.8,
                        "event_type": "FACT",
                        "status": "active",
                    }
                ),
            }
        ]

        self.mock_faiss_manager.db.document_storage.get_documents = AsyncMock(
            return_value=mock_docs
        )

        result = await self.handler.get_memory_details("123")

        assert result["success"] is True
        data = result["data"]
        assert data["id"] == "123"
        assert data["content"] == "测试记忆内容"
        assert data["importance"] == 0.8
        assert data["event_type"] == "FACT"
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_memory_details_not_found(self):
        """测试获取不存在的记忆详细信息"""
        self.mock_faiss_manager.db.document_storage.get_documents = AsyncMock(
            return_value=[]
        )

        result = await self.handler.get_memory_details("999")

        assert result["success"] is False
        assert "未找到ID为 999 的记忆" in result["message"]

    @pytest.mark.asyncio
    async def test_get_memory_history_success(self):
        """测试获取记忆历史（成功）"""
        mock_docs = [
            {
                "id": 123,
                "content": "测试记忆内容",
                "metadata": json.dumps(
                    {
                        "create_time": 1609459200.0,
                        "importance": 0.8,
                        "event_type": "FACT",
                        "status": "active",
                        "update_history": [
                            {
                                "timestamp": 1609459200.0,
                                "reason": "初始创建",
                                "fields": ["content", "importance"],
                            }
                        ],
                    }
                ),
            }
        ]

        self.mock_faiss_manager.db.document_storage.get_documents = AsyncMock(
            return_value=mock_docs
        )

        result = await self.handler.get_memory_history("123")

        assert result["success"] is True
        data = result["data"]
        assert len(data["update_history"]) == 1
        assert data["update_history"][0]["reason"] == "初始创建"

    def test_format_memory_details_for_display_success(self):
        """测试格式化记忆详细信息显示（成功）"""
        mock_response = {
            "success": True,
            "data": {
                "id": "123",
                "content": "测试记忆内容",
                "importance": 0.8,
                "event_type": "FACT",
                "status": "active",
                "create_time": "2021-01-01 00:00:00",
                "last_access_time": "2021-01-01 00:00:00",
                "update_history": [],
            },
        }

        result = self.handler.format_memory_details_for_display(mock_response)

        assert "📝 记忆 123 的详细信息:" in result
        assert "测试记忆内容" in result
        assert "重要性: 0.8" in result
        assert "类型: FACT" in result
        assert "状态: active" in result

    def test_format_memory_details_for_display_failure(self):
        """测试格式化记忆详细信息显示（失败）"""
        mock_response = {"success": False, "message": "获取失败"}

        result = self.handler.format_memory_details_for_display(mock_response)

        assert result == "获取失败"

    def test_format_memory_history_for_display_success(self):
        """测试格式化记忆历史显示（成功）"""
        mock_response = {
            "success": True,
            "data": {
                "id": "123",
                "content": "测试记忆内容",
                "metadata": {
                    "importance": 0.8,
                    "event_type": "FACT",
                    "status": "active",
                    "create_time": "2021-01-01 00:00:00",
                },
                "update_history": [
                    {
                        "timestamp": 1609459200.0,
                        "reason": "初始创建",
                        "fields": ["content"],
                    }
                ],
            },
        }

        result = self.handler.format_memory_history_for_display(mock_response)

        assert "📝 记忆 123 的详细信息:" in result
        assert "测试记忆内容" in result
        assert "🔄 更新历史 (1 次):" in result
        assert "初始创建" in result

    def test_format_memory_history_for_display_no_history(self):
        """测试格式化记忆历史显示（无历史记录）"""
        mock_response = {
            "success": True,
            "data": {
                "id": "123",
                "content": "测试记忆内容",
                "metadata": {
                    "importance": 0.8,
                    "event_type": "FACT",
                    "status": "active",
                    "create_time": "2021-01-01 00:00:00",
                },
                "update_history": [],
            },
        }

        result = self.handler.format_memory_history_for_display(mock_response)

        assert "🔄 暂无更新记录" in result
