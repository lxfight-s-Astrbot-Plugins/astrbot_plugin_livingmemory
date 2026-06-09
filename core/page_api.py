"""
官方插件 Page API 适配层。

职责：
1. 为 AstrBot 官方插件页面注册原生 Web API。
2. 直接复用插件运行期组件，不再代理到旧 FastAPI WebUI。
3. 保留返回结构与旧前端尽量一致，降低页面迁移成本。
"""

from __future__ import annotations

from typing import Any

from .page_api_modules import (
    BackupHandler,
    GraphHandler,
    MemoryHandler,
    PageApiUtils,
    RecallHandler,
    StatsHandler,
)

PLUGIN_NAME = "astrbot_plugin_livingmemory"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """LivingMemory 官方插件页面 API（Facade）。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

        # 初始化工具类
        self.utils = PageApiUtils()

        # 初始化各个处理器
        self.stats_handler = StatsHandler(self.utils)
        self.memory_handler = MemoryHandler(self.utils)
        self.recall_handler = RecallHandler(self.utils)
        self.graph_handler = GraphHandler(self.utils)

        # BackupHandler 需要 data_dir，延迟初始化
        self._backup_handler = None

    @property
    def backup_handler(self) -> BackupHandler:
        """延迟初始化 BackupHandler"""
        if self._backup_handler is None:
            data_dir = (
                self.plugin.initializer.data_dir if self.plugin.initializer else ""
            )
            self._backup_handler = BackupHandler(self.utils, data_dir)
        return self._backup_handler

    def register_routes(self) -> None:
        """注册官方插件页面所需的原生 API。"""
        register = self.plugin.context.register_web_api
        register(
            f"{PAGE_API_PREFIX}/stats",
            self.get_stats,
            ["GET"],
            "LivingMemory Page stats",
        )
        register(
            f"{PAGE_API_PREFIX}/memories",
            self.list_memories,
            ["GET"],
            "LivingMemory Page memories",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/detail",
            self.get_memory_detail,
            ["GET"],
            "LivingMemory Page memory detail",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/update",
            self.update_memory,
            ["POST"],
            "LivingMemory Page update memory",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-delete",
            self.batch_delete_memories,
            ["POST"],
            "LivingMemory Page batch delete memories",
        )
        register(
            f"{PAGE_API_PREFIX}/memories/batch-update",
            self.batch_update_memories,
            ["POST"],
            "LivingMemory Page batch update memories",
        )
        register(
            f"{PAGE_API_PREFIX}/recall/test",
            self.test_recall,
            ["POST"],
            "LivingMemory Page recall test",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/overview",
            self.get_graph_overview,
            ["GET"],
            "LivingMemory Page graph overview",
        )
        register(
            f"{PAGE_API_PREFIX}/graph/query",
            self.query_graph,
            ["POST"],
            "LivingMemory Page graph query",
        )
        register(
            f"{PAGE_API_PREFIX}/backups",
            self.list_backups,
            ["GET"],
            "LivingMemory Page backup list",
        )

    # ==================== 路由处理方法 ====================
    # 所有方法都委托给相应的处理器

    async def get_stats(self):
        """获取插件统计信息"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.stats_handler.get_stats(ready["memory_engine"])

    async def list_memories(self):
        """获取记忆列表（带分页和过滤）"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.list_memories(ready["memory_engine"])

    async def get_memory_detail(self):
        """获取单个记忆的完整详情"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.get_memory_detail(ready["memory_engine"])

    async def update_memory(self):
        """更新单个记忆的字段"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.update_memory(ready["memory_engine"])

    async def batch_delete_memories(self):
        """批量删除记忆"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.batch_delete_memories(ready["memory_engine"])

    async def batch_update_memories(self):
        """批量更新记忆字段"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.batch_update_memories(ready["memory_engine"])

    async def test_recall(self):
        """测试记忆召回功能"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.recall_handler.test_recall(ready["memory_engine"])

    async def get_graph_overview(self):
        """获取图谱概览"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.graph_handler.get_graph_overview(ready["memory_engine"])

    async def query_graph(self):
        """查询图谱"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.graph_handler.query_graph(ready["memory_engine"])

    async def list_backups(self):
        """列出所有版本备份及其元数据"""
        return await self.backup_handler.list_backups()

    # ==================== 辅助方法 ====================

    async def _ensure_plugin_ready(self) -> tuple[dict[str, Any] | None, dict | None]:
        """
        确保插件已就绪

        Returns:
            (ready_dict, error_dict) 元组
            - ready_dict: 包含 memory_engine 等组件的字典
            - error_dict: 错误响应字典（如果有错误）
        """
        ready, message = await self.plugin._ensure_plugin_ready()
        if not ready:
            return None, self.utils.error(message or "插件尚未就绪")

        memory_engine = self.plugin.initializer.memory_engine
        if memory_engine is None:
            return None, self.utils.error("记忆引擎未初始化")

        return {
            "memory_engine": memory_engine,
            "conversation_manager": self.plugin.initializer.conversation_manager,
            "index_validator": self.plugin.initializer.index_validator,
        }, None
