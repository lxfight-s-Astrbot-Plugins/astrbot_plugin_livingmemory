# -*- coding: utf-8 -*-
"""
命令装饰器 - 提供命令处理的通用装饰器
"""

from functools import wraps
from typing import List, Optional
import asyncio

from astrbot.api import logger


def require_handlers(*handler_names: str):
    """
    装饰器：检查所需的 Handler 是否已初始化

    用法:
        @require_handlers("admin_handler", "search_handler")
        async def lmem_status(self, event: AstrMessageEvent):
            result = await self.admin_handler.get_memory_status()
            yield event.plain_result(...)

    参数:
        *handler_names: 需要检查的 handler 属性名列表

    功能:
        1. 等待插件整体初始化完成 (最多 5 秒)
        2. 检查所有指定的 handler 是否已初始化
        3. 如果未初始化，返回友好的错误消息
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, event, *args, **kwargs):
            # 检查整体初始化状态
            if hasattr(self, '_wait_for_initialization'):
                try:
                    initialized = await asyncio.wait_for(
                        self._wait_for_initialization(),
                        timeout=5.0
                    )
                    if not initialized:
                        yield event.plain_result("⏳ 插件正在初始化中，请稍候...")
                        return
                except asyncio.TimeoutError:
                    yield event.plain_result("⏳ 插件初始化超时，请稍后重试...")
                    return

            # 检查所需的 Handlers
            for handler_name in handler_names:
                handler = getattr(self, handler_name, None)
                if not handler:
                    # 格式化 handler 名称为友好的中文名
                    friendly_names = {
                        "admin_handler": "管理员处理器",
                        "memory_handler": "记忆处理器",
                        "search_handler": "搜索处理器",
                        "fusion_handler": "融合处理器"
                    }
                    friendly_name = friendly_names.get(handler_name, handler_name)
                    yield event.plain_result(f"❌ {friendly_name}尚未初始化")
                    return

            # 调用原函数
            async for result in func(self, event, *args, **kwargs):
                yield result

        return wrapper
    return decorator


def handle_command_errors(func):
    """
    装饰器：统一处理命令执行中的异常

    用法:
        @handle_command_errors
        @require_handlers("admin_handler")
        async def lmem_status(self, event: AstrMessageEvent):
            result = await self.admin_handler.get_memory_status()
            yield event.plain_result(...)

    功能:
        1. 捕获命令执行中的所有异常
        2. 记录详细的错误日志
        3. 向用户返回友好的错误消息
    """
    @wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        try:
            async for result in func(self, event, *args, **kwargs):
                yield result
        except asyncio.CancelledError:
            # 允许异步任务被取消
            raise
        except Exception as e:
            # 记录详细错误日志
            session_id = getattr(event, 'session_id', 'unknown')
            logger.error(
                f"[{session_id}] 命令执行失败: {func.__name__}, "
                f"错误: {type(e).__name__}: {str(e)}",
                exc_info=True
            )

            # 返回友好的错误消息
            error_msg = f"❌ 命令执行失败: {str(e)}"
            yield event.plain_result(error_msg)

    return wrapper


def deprecated(new_command: str, version: Optional[str] = None):
    """
    装饰器：标记命令为已废弃

    用法:
        @deprecated("/lmem info", version="1.4.0")
        @lmem_group.command("update")
        async def lmem_update(self, event: AstrMessageEvent, memory_id: str):
            async for result in self.lmem_info(event, memory_id):
                yield result

    参数:
        new_command: 新命令的名称
        version: 废弃的版本号（可选）

    功能:
        1. 在命令执行前显示废弃警告
        2. 提示用户使用新命令
        3. 继续执行原有功能
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, event, *args, **kwargs):
            # 构建警告消息
            warning_msg = f"⚠️ 此命令已废弃，请使用 {new_command} 替代"
            if version:
                warning_msg += f" (自 v{version} 起)"

            yield event.plain_result(warning_msg)

            # 执行原有功能
            async for result in func(self, event, *args, **kwargs):
                yield result

        return wrapper
    return decorator
