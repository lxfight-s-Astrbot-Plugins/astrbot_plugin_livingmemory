# -*- coding: utf-8 -*-
"""
test_admin_handler.py - 管理员处理器测试
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from core.handlers.admin_handler import AdminHandler
from tests.conftest import TEST_CONFIG


class TestAdminHandler:
    """管理员处理器测试类"""

    def setup_method(self):
        """测试前设置"""
        self.mock_context = Mock()
        self.mock_faiss_manager = Mock()
        self.mock_forgetting_agent = Mock()
        self.mock_session_manager = Mock()
        self.handler = AdminHandler(
            self.mock_context,
            TEST_CONFIG,
            self.mock_faiss_manager,
            self.mock_forgetting_agent,
            self.mock_session_manager,
        )

    @pytest.mark.asyncio
    async def test_get_memory_status_success(self):
        """测试获取记忆库状态（成功）"""
        # 模拟数据库计数
        self.mock_faiss_manager.db.count_documents = AsyncMock(return_value=42)

        result = await self.handler.get_memory_status()

        assert result["success"] is True
        assert result["data"]["total_count"] == 42

        # 验证调用
        self.mock_faiss_manager.db.count_documents.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_memory_status_no_manager(self):
        """测试没有管理器时获取状态"""
        handler = AdminHandler(self.mock_context, TEST_CONFIG, None, None, None)

        result = await handler.get_memory_status()

        assert result["success"] is False
        assert "记忆库尚未初始化" in result["message"]

    @pytest.mark.asyncio
    async def test_get_memory_status_exception(self):
        """测试获取记忆库状态异常处理"""
        self.mock_faiss_manager.db.count_documents = AsyncMock(
            side_effect=Exception("数据库错误")
        )

        result = await self.handler.get_memory_status()

        assert result["success"] is False
        assert "获取记忆库状态失败" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_memory_success(self):
        """测试删除记忆（成功）"""
        self.mock_faiss_manager.delete_memories = AsyncMock()

        result = await self.handler.delete_memory(123)

        assert result["success"] is True
        assert "已成功删除 ID 为 123 的记忆" in result["message"]

        # 验证调用参数
        self.mock_faiss_manager.delete_memories.assert_called_once_with([123])

    @pytest.mark.asyncio
    async def test_delete_memory_no_manager(self):
        """测试没有管理器时删除记忆"""
        handler = AdminHandler(self.mock_context, TEST_CONFIG, None, None, None)

        result = await handler.delete_memory(123)

        assert result["success"] is False
        assert "记忆库尚未初始化" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_memory_exception(self):
        """测试删除记忆异常处理"""
        self.mock_faiss_manager.delete_memories = AsyncMock(
            side_effect=Exception("删除错误")
        )

        result = await self.handler.delete_memory(123)

        assert result["success"] is False
        assert "删除记忆时发生错误" in result["message"]

    @pytest.mark.asyncio
    async def test_run_forgetting_agent_success(self):
        """测试运行遗忘代理（成功）"""
        self.mock_forgetting_agent._prune_memories = AsyncMock()

        result = await self.handler.run_forgetting_agent()

        assert result["success"] is True
        assert "遗忘代理任务执行完毕" in result["message"]

        # 验证调用
        self.mock_forgetting_agent._prune_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_forgetting_agent_no_agent(self):
        """测试没有遗忘代理时运行"""
        handler = AdminHandler(self.mock_context, TEST_CONFIG, None, None, None)

        result = await handler.run_forgetting_agent()

        assert result["success"] is False
        assert "遗忘代理尚未初始化" in result["message"]

    @pytest.mark.asyncio
    async def test_run_forgetting_agent_exception(self):
        """测试运行遗忘代理异常处理"""
        self.mock_forgetting_agent._prune_memories = AsyncMock(
            side_effect=Exception("遗忘代理错误")
        )

        result = await self.handler.run_forgetting_agent()

        assert result["success"] is False
        assert "遗忘代理任务执行失败" in result["message"]

    @pytest.mark.asyncio
    async def test_set_search_mode_valid(self):
        """测试设置搜索模式（有效模式）"""
        result = await self.handler.set_search_mode("hybrid")

        assert result["success"] is True
        assert "检索模式已设置为: hybrid" in result["message"]

    @pytest.mark.asyncio
    async def test_set_search_mode_invalid(self):
        """测试设置搜索模式（无效模式）"""
        result = await self.handler.set_search_mode("invalid_mode")

        assert result["success"] is False
        assert "无效的模式" in result["message"]
        assert "hybrid, dense, sparse" in result["message"]

    @pytest.mark.asyncio
    async def test_get_config_summary_show(self):
        """测试获取配置摘要（显示）"""
        # 模拟会话管理器
        self.mock_session_manager.get_session_count = Mock(return_value=5)

        result = await self.handler.get_config_summary("show")

        assert result["success"] is True
        data = result["data"]

        # 验证各个配置部分
        assert "session_manager" in data
        assert "recall_engine" in data
        assert "reflection_engine" in data
        assert "forgetting_agent" in data

        # 验证具体配置值
        assert data["session_manager"]["max_sessions"] == 100
        assert data["session_manager"]["session_ttl"] == 3600
        assert data["session_manager"]["current_sessions"] == 5
        assert data["recall_engine"]["retrieval_mode"] == "hybrid"
        assert data["recall_engine"]["top_k"] == 5
        assert data["forgetting_agent"]["enabled"] is True

        # 验证调用
        self.mock_session_manager.get_session_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_config_summary_validate_success(self):
        """测试获取配置摘要（验证成功）"""
        with patch("core.config_validator.validate_config") as mock_validate:
            mock_validate.return_value = None  # 验证通过时不返回异常

            result = await self.handler.get_config_summary("validate")

            assert result["success"] is True
            assert "配置验证通过，所有参数均有效" in result["message"]

            # 验证调用
            mock_validate.assert_called_once_with(TEST_CONFIG)

    @pytest.mark.asyncio
    async def test_get_config_summary_validate_failure(self):
        """测试获取配置摘要（验证失败）"""
        with patch("core.config_validator.validate_config") as mock_validate:
            mock_validate.side_effect = ValueError("配置验证失败")

            result = await self.handler.get_config_summary("validate")

            assert result["success"] is False
            assert "配置验证失败" in result["message"]

            # 验证调用
            mock_validate.assert_called_once_with(TEST_CONFIG)

    @pytest.mark.asyncio
    async def test_get_config_summary_invalid_action(self):
        """测试获取配置摘要（无效动作）"""
        result = await self.handler.get_config_summary("invalid_action")

        assert result["success"] is False
        assert "无效的动作" in result["message"]
        assert "show" in result["message"]
        assert "validate" in result["message"]

    @pytest.mark.asyncio
    async def test_get_config_summary_show_exception(self):
        """测试获取配置摘要显示异常处理"""
        self.mock_session_manager.get_session_count = Mock(
            side_effect=Exception("配置错误")
        )

        result = await self.handler.get_config_summary("show")

        assert result["success"] is False
        assert "显示配置时发生错误" in result["message"]

    def test_format_status_for_display_success(self):
        """测试格式化状态显示（成功）"""
        mock_response = {"success": True, "data": {"total_count": 42}}

        result = self.handler.format_status_for_display(mock_response)

        assert "📊 LivingMemory 记忆库状态：" in result
        assert "- 总记忆数: 42" in result

    def test_format_status_for_display_failure(self):
        """测试格式化状态显示（失败）"""
        mock_response = {"success": False, "message": "获取失败"}

        result = self.handler.format_status_for_display(mock_response)

        assert result == "获取失败"

    def test_format_config_summary_for_display_success(self):
        """测试格式化配置摘要显示（成功）"""
        mock_response = {
            "success": True,
            "data": {
                "session_manager": {
                    "max_sessions": 1000,
                    "session_ttl": 3600,
                    "current_sessions": 5,
                },
                "recall_engine": {
                    "retrieval_mode": "hybrid",
                    "top_k": 5,
                    "recall_strategy": "weighted",
                },
                "reflection_engine": {
                    "summary_trigger_rounds": 10,
                    "importance_threshold": 0.5,
                },
                "forgetting_agent": {
                    "enabled": True,
                    "check_interval_hours": 24,
                    "retention_days": 90,
                },
            },
        }

        result = self.handler.format_config_summary_for_display(mock_response)

        assert "📋 LivingMemory 配置摘要:" in result
        assert "🗂️ 会话管理:" in result
        assert "🧠 回忆引擎:" in result
        assert "💭 反思引擎:" in result
        assert "🗑️ 遗忘代理:" in result
        assert "最大会话数: 1000" in result
        assert "会话TTL: 3600秒" in result
        assert "当前会话数: 5" in result
        assert "检索模式: hybrid" in result
        assert "返回数量: 5" in result
        assert "启用状态: 是" in result
        assert "检查间隔: 24小时" in result
        assert "保留天数: 90天" in result

    def test_format_config_summary_for_display_failure(self):
        """测试格式化配置摘要显示（失败）"""
        mock_response = {"success": False, "message": "配置获取失败"}

        result = self.handler.format_config_summary_for_display(mock_response)

        assert result == "配置获取失败"

    def test_format_config_summary_for_display_missing_sections(self):
        """测试格式化配置摘要显示（缺少部分配置）"""
        mock_response = {
            "success": True,
            "data": {
                "session_manager": {
                    "max_sessions": 1000,
                    "session_ttl": 3600,
                    "current_sessions": 5,
                }
                # 缺少其他配置部分
            },
        }

        result = self.handler.format_config_summary_for_display(mock_response)

        assert "📋 LivingMemory 配置摘要:" in result
        assert "🗂️ 会话管理:" in result
        # 方法总是显示所有配置部分，使用默认值
        assert "🧠 回忆引擎:" in result
        assert "💭 反思引擎:" in result
        assert "🗑️ 遗忘代理:" in result
