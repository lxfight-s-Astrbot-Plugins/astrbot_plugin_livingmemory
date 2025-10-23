# -*- coding: utf-8 -*-
"""
test_decorators.py - 测试命令装饰器
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from core.commands.decorators import require_handlers, handle_command_errors, deprecated


class MockEvent:
    """模拟 AstrMessageEvent"""
    def __init__(self):
        self.results = []

    def plain_result(self, text):
        self.results.append(text)
        return text


class MockPlugin:
    """模拟插件类"""
    def __init__(self):
        self.admin_handler = None
        self.search_handler = None
        self.memory_handler = None
        self._initialization_done = asyncio.Event()

    async def _wait_for_initialization(self):
        """模拟初始化等待"""
        await self._initialization_done.wait()
        return True


@pytest.mark.asyncio
class TestRequireHandlers:
    """测试 require_handlers 装饰器"""

    async def test_handler_not_initialized(self):
        """测试 handler 未初始化的情况"""
        plugin = MockPlugin()
        plugin._initialization_done.set()
        event = MockEvent()

        @require_handlers("admin_handler")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证返回了错误消息
        assert len(results) == 1
        assert "管理员处理器尚未初始化" in results[0]

    async def test_handler_initialized(self):
        """测试 handler 已初始化的情况"""
        plugin = MockPlugin()
        plugin.admin_handler = Mock()  # 初始化 handler
        plugin._initialization_done.set()
        event = MockEvent()

        @require_handlers("admin_handler")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证命令正常执行
        assert len(results) == 1
        assert "命令执行成功" in results[0]

    async def test_multiple_handlers(self):
        """测试多个 handler 检查"""
        plugin = MockPlugin()
        plugin.admin_handler = Mock()
        plugin.search_handler = Mock()
        plugin._initialization_done.set()
        event = MockEvent()

        @require_handlers("admin_handler", "search_handler")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证命令正常执行
        assert len(results) == 1
        assert "命令执行成功" in results[0]

    async def test_one_handler_missing(self):
        """测试其中一个 handler 缺失"""
        plugin = MockPlugin()
        plugin.admin_handler = Mock()
        # search_handler 未初始化
        plugin._initialization_done.set()
        event = MockEvent()

        @require_handlers("admin_handler", "search_handler")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证返回了错误消息
        assert len(results) == 1
        assert "搜索处理器尚未初始化" in results[0]

    async def test_initialization_timeout(self):
        """测试初始化超时"""
        plugin = MockPlugin()
        # 不设置初始化完成标志，模拟超时
        event = MockEvent()

        @require_handlers("admin_handler")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证返回了超时消息
        assert len(results) == 1
        assert "初始化超时" in results[0] or "正在初始化" in results[0]


@pytest.mark.asyncio
class TestHandleCommandErrors:
    """测试 handle_command_errors 装饰器"""

    async def test_normal_execution(self):
        """测试正常执行"""
        plugin = MockPlugin()
        event = MockEvent()

        @handle_command_errors
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证命令正常执行
        assert len(results) == 1
        assert "命令执行成功" in results[0]

    async def test_exception_handling(self):
        """测试异常处理"""
        plugin = MockPlugin()
        event = MockEvent()
        event.session_id = "test_session"

        @handle_command_errors
        async def test_command(self, event):
            raise ValueError("测试错误")
            yield event.plain_result("不应该执行到这里")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证返回了错误消息
        assert len(results) == 1
        assert "命令执行失败" in results[0]
        assert "测试错误" in results[0]


@pytest.mark.asyncio
class TestDeprecated:
    """测试 deprecated 装饰器"""

    async def test_deprecated_warning(self):
        """测试废弃警告"""
        plugin = MockPlugin()
        event = MockEvent()

        @deprecated("/lmem new_command", version="1.4.0")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证显示了警告和正常结果
        assert len(results) == 2
        assert "已废弃" in results[0]
        assert "/lmem new_command" in results[0]
        assert "v1.4.0" in results[0]
        assert "命令执行成功" in results[1]

    async def test_deprecated_without_version(self):
        """测试不带版本号的废弃警告"""
        plugin = MockPlugin()
        event = MockEvent()

        @deprecated("/lmem new_command")
        async def test_command(self, event):
            yield event.plain_result("命令执行成功")

        # 执行命令
        results = []
        async for result in test_command(plugin, event):
            results.append(result)

        # 验证显示了警告
        assert len(results) == 2
        assert "已废弃" in results[0]
        assert "/lmem new_command" in results[0]
