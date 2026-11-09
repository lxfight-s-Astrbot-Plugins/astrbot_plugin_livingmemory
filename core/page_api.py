"""
官方插件 Page API 适配层。

职责：
1. 为 AstrBot 官方插件页面注册原生 Web API。
2. 直接复用插件运行期组件，不再代理到旧 FastAPI WebUI。
3. 保留返回结构与旧前端尽量一致，降低页面迁移成本。
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from quart import request

from .memory_scope import adapter_access
from .page_api_modules import (
    BackupHandler,
    ConsolidationHandler,
    ExternalHandler,
    GraphHandler,
    MemoryHandler,
    PageApiUtils,
    ProfileHandler,
    PromptHandler,
    RecallHandler,
    SettingsHandler,
    StatsHandler,
    TraceHandler,
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
        self.trace_handler = TraceHandler(self.utils)
        self.memory_handler = MemoryHandler(self.utils)
        self.recall_handler = RecallHandler(self.utils)
        self.graph_handler = GraphHandler(self.utils)
        self.prompt_handler = PromptHandler(self.utils, plugin)
        self.profile_handler = ProfileHandler(self.utils)
        self.consolidation_handler = ConsolidationHandler(self.utils)
        self.settings_handler = SettingsHandler(self.utils, plugin)
        self.external_handler = ExternalHandler(self.utils, plugin)

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

    # (路径后缀, 处理方法, HTTP 方法, 描述)；按声明顺序注册
    _ROUTES = (
        (
            "readiness",
            "get_readiness",
            ["GET"],
            "LivingMemory Page initialization readiness",
        ),
        ("stats", "get_stats", ["GET"], "LivingMemory Page stats"),
        (
            "adapters/assign",
            "settings_handler.assign_adapter",
            ["POST"],
            "Assign adapter ownership to legacy memories",
        ),
        ("memories", "list_memories", ["GET"], "LivingMemory Page memories"),
        (
            "memories/detail",
            "get_memory_detail",
            ["GET"],
            "LivingMemory Page memory detail",
        ),
        (
            "memories/update",
            "update_memory",
            ["POST"],
            "LivingMemory Page update memory",
        ),
        (
            "memories/resummarize",
            "resummarize_memory",
            ["POST"],
            "LivingMemory Page resummarize memory source",
        ),
        (
            "memories/export",
            "export_memories",
            ["POST"],
            "LivingMemory Page export memories",
        ),
        (
            "memories/import",
            "import_memories",
            ["POST"],
            "LivingMemory Page import memories",
        ),
        (
            "memories/batch-delete",
            "batch_delete_memories",
            ["POST"],
            "LivingMemory Page batch delete memories",
        ),
        (
            "memories/batch-update",
            "batch_update_memories",
            ["POST"],
            "LivingMemory Page batch update memories",
        ),
        ("recall/test", "test_recall", ["POST"], "LivingMemory Page recall test"),
        ("traces", "list_traces", ["GET"], "LivingMemory Page recall traces"),
        (
            "traces/stats",
            "get_trace_stats",
            ["GET"],
            "LivingMemory Page recall trace statistics",
        ),
        (
            "traces/<trace_id>",
            "get_trace",
            ["GET"],
            "LivingMemory Page recall trace detail",
        ),
        (
            "memories/<memory_id>/lineage",
            "get_memory_lineage",
            ["GET"],
            "LivingMemory Page memory lineage",
        ),
        (
            "graph/overview",
            "get_graph_overview",
            ["GET"],
            "LivingMemory Page graph overview",
        ),
        ("graph/query", "query_graph", ["POST"], "LivingMemory Page graph query"),
        ("backups", "list_backups", ["GET"], "LivingMemory Page backup list"),
        ("prompts", "list_prompts", ["GET"], "LivingMemory Page prompt list"),
        (
            "prompts/detail",
            "get_prompt_detail",
            ["GET"],
            "LivingMemory Page prompt detail",
        ),
        (
            "prompts/update",
            "update_prompt",
            ["POST"],
            "LivingMemory Page update prompt",
        ),
        (
            "prompts/reset",
            "reset_prompt",
            ["POST"],
            "LivingMemory Page reset prompt",
        ),
        (
            "prompts/default",
            "get_prompt_default",
            ["GET"],
            "LivingMemory Page get prompt default content",
        ),
        ("config", "get_settings_config", ["GET"], "LivingMemory Page advanced config"),
        (
            "config/update",
            "update_settings_config",
            ["POST"],
            "LivingMemory Page update advanced config",
        ),
        (
            "consolidation/status",
            "get_consolidation_status",
            ["GET"],
            "LivingMemory Page consolidation status",
        ),
        (
            "consolidation/run",
            "run_consolidation",
            ["POST"],
            "LivingMemory Page run consolidation",
        ),
        ("profiles", "list_profiles", ["GET"], "LivingMemory Page profiles"),
        (
            "profiles/detail",
            "get_profile_detail",
            ["GET"],
            "LivingMemory Page profile detail",
        ),
        (
            "profiles/history",
            "list_profile_history",
            ["GET"],
            "LivingMemory Page profile history",
        ),
        (
            # PUT 保持 REST 语义；POST 是宿主 Page API bridge 唯一支持的方法
            # （bridge 只处理 apiGet/apiPost，插件 iframe 无法发 PUT/DELETE）。
            "profiles/update",
            "update_profile",
            ["PUT", "POST"],
            "LivingMemory Page update profile",
        ),
        (
            "profiles/delete",
            "delete_profile",
            ["DELETE", "POST"],
            "LivingMemory Page delete profile",
        ),
        (
            "profiles/pending",
            "list_profile_pending",
            ["GET"],
            "LivingMemory Page profile pending candidates",
        ),
        (
            "profiles/pending/resolve",
            "resolve_profile_pending",
            ["POST"],
            "LivingMemory Page resolve profile pending candidate",
        ),
        (
            "external/keys",
            "external_handler.list_keys",
            ["GET"],
            "LivingMemory external API key list",
        ),
        (
            "external/keys",
            "external_handler.create_key",
            ["POST"],
            "LivingMemory external API key create (token returned once)",
        ),
        (
            "external/keys/<key_id>",
            "external_handler.revoke_key",
            ["DELETE"],
            "LivingMemory external API key revoke",
        ),
        (
            "external/health",
            "external_handler.handle_health",
            ["GET"],
            "LivingMemory external API health",
        ),
        (
            "external/personas",
            "external_handler.handle_personas",
            ["GET"],
            "LivingMemory external API personas and scopes",
        ),
        (
            "external/recall",
            "external_handler.handle_recall",
            ["POST"],
            "LivingMemory external API recall",
        ),
        (
            "external/memories",
            "external_handler.handle_write_memory",
            ["POST"],
            "LivingMemory external API write memory",
        ),
        (
            "external/memories/<memory_id>",
            "external_handler.handle_get_memory",
            ["GET"],
            "LivingMemory external API get memory",
        ),
        (
            "external/memories/<memory_id>/archive",
            "external_handler.handle_archive_memory",
            ["POST"],
            "LivingMemory external API archive memory",
        ),
    )

    def register_routes(self) -> None:
        """注册官方插件页面所需的原生 API。"""
        for suffix, handler_path, methods, description in self._ROUTES:
            handler = self._resolve_handler(handler_path)
            self.plugin.context.register_web_api(
                f"{PAGE_API_PREFIX}/{suffix}",
                self._scoped(handler),
                methods,
                description,
            )

    def _resolve_handler(self, path: str):
        """解析 ``方法`` 或 ``属性.方法`` 形式的处理函数。"""
        target = self
        for part in path.split("."):
            target = getattr(target, part)
        return target

    def _scoped(self, handler):
        """把页面请求的 adapter_id 映射为一次请求局部的访问范围。"""

        @wraps(handler)
        async def scoped_handler(*args, **kwargs):
            adapter_id = request.args.get("adapter_id")
            if not adapter_id:
                return await handler(*args, **kwargs)
            with adapter_access(self.plugin.config_manager, adapter_id):
                return await handler(*args, **kwargs)

        return scoped_handler

    # ==================== 路由处理方法 ====================
    # 所有方法都委托给相应的处理器

    async def get_readiness(self):
        """返回初始化/Provider 就绪状态（不等待，供前端横幅轮询）。"""
        ready, message = await self.plugin._ensure_plugin_ready(wait=False)
        initializer = getattr(self.plugin, "initializer", None)
        is_failed = bool(getattr(initializer, "is_failed", False))
        state = "ready" if ready else ("failed" if is_failed else "in_progress")
        return self.utils.ok(
            {
                "ready": bool(ready),
                "state": state,
                "message": "" if ready else message,
            }
        )

    async def get_stats(self):
        """获取插件统计信息"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.stats_handler.get_stats(ready["memory_engine"])

    async def list_memories(self):
        """获取记忆列表（带分页和过滤）"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.memory_handler.list_memories(ready["memory_engine"])

    async def get_memory_detail(self):
        """获取单个记忆的完整详情"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.memory_handler.get_memory_detail(ready["memory_engine"])

    async def update_memory(self):
        """更新单个记忆的字段"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.update_memory(ready["memory_engine"])

    async def resummarize_memory(self):
        """Regenerate one memory from its retained source messages."""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.resummarize_memory(
            ready["memory_engine"], ready["memory_processor"]
        )

    async def export_memories(self):
        """Export all or selected memories (read-only, never blocks on init)."""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.memory_handler.export_memories(ready["memory_engine"])

    async def import_memories(self):
        """Preview or import portable memory data."""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.memory_handler.import_memories(
            ready["memory_engine"],
            ready["memory_processor"],
            current_platforms=self._current_platform_names(),
            existing_session_ids=await self._existing_session_ids(
                ready["conversation_manager"]
            ),
        )

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
        """测试记忆召回功能（只读检索，不阻塞等待初始化）"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.recall_handler.test_recall(ready["memory_engine"])

    async def get_graph_overview(self):
        """获取图谱概览"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.graph_handler.get_graph_overview(ready["memory_engine"])

    async def query_graph(self):
        """查询图谱（只读检索，不阻塞等待初始化）"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.graph_handler.query_graph(ready["memory_engine"])

    async def list_backups(self):
        """列出所有版本备份及其元数据"""
        return await self.backup_handler.list_backups()

    # ---- 召回追踪与血缘路由 ----

    async def list_traces(self):
        """列出最近的召回追踪摘要。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.trace_handler.list_traces(ready["memory_engine"])

    async def get_trace_stats(self):
        """返回召回追踪聚合统计。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.trace_handler.get_trace_stats(ready["memory_engine"])

    async def get_trace(self, trace_id: str = ""):
        """返回单条召回追踪详情。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.trace_handler.get_trace(ready["memory_engine"], trace_id)

    async def get_memory_lineage(self, memory_id: str = ""):
        """返回单条记忆的血缘（来源/取代链/图关联/命中追踪）。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.trace_handler.get_lineage(ready["memory_engine"], memory_id)

    # ---- Prompt 管理路由 ----

    async def list_prompts(self):
        return await self.prompt_handler.list_prompts()

    async def get_prompt_detail(self):
        return await self.prompt_handler.get_prompt_detail()

    async def update_prompt(self):
        return await self.prompt_handler.update_prompt()

    async def reset_prompt(self):
        return await self.prompt_handler.reset_prompt()

    async def get_prompt_default(self):
        return await self.prompt_handler.get_prompt_default()

    # ---- 记忆整合路由 ----

    async def get_consolidation_status(self):
        """获取记忆整合配置与统计"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.consolidation_handler.get_status(
            ready["memory_engine"],
            ready["consolidation_manager"],
            ready["config_manager"],
        )

    async def run_consolidation(self):
        """手动触发一轮记忆整合"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.consolidation_handler.run(ready["consolidation_manager"])

    # ---- 设置页路由 ----

    async def get_settings_config(self):
        """Return advanced configuration sections for the dashboard settings page."""
        return await self.settings_handler.get_config()

    async def update_settings_config(self):
        """Persist advanced configuration updates from the dashboard settings page."""
        return await self.settings_handler.update_config()

    # ---- 用户档案路由 ----

    async def list_profiles(self):
        """列出用户档案（可选 scope/persona_id 过滤）。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.profile_handler.list_profiles(ready["memory_engine"])

    async def get_profile_detail(self):
        """获取单个用户档案。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.profile_handler.get_profile_detail(ready["memory_engine"])

    async def list_profile_history(self):
        """获取用户档案历史版本。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.profile_handler.list_profile_history(ready["memory_engine"])

    async def update_profile(self):
        """编辑用户档案（写入历史，version+1）。"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.profile_handler.update_profile(ready["memory_engine"])

    async def delete_profile(self):
        """删除用户档案及其历史。"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.profile_handler.delete_profile(ready["memory_engine"])

    async def list_profile_pending(self):
        """列出待确认档案候选。"""
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.profile_handler.list_profile_pending(ready["memory_engine"])

    async def resolve_profile_pending(self):
        """接受/拒绝待确认档案候选。"""
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.profile_handler.resolve_profile_pending(
            ready["memory_engine"]
        )

    # ---- 外部记忆 API 路由 ----

    async def external_list_keys(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.list_keys(ready)

    async def external_create_key(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.create_key(ready)

    async def external_revoke_key(self, key_id: str):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.revoke_key(ready, key_id)

    async def external_health(self):
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.external_handler.handle_health()

    async def external_personas(self):
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.external_handler.handle_personas()

    async def external_recall(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.handle_recall()

    async def external_write_memory(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.handle_write_memory()

    async def external_get_memory(self, memory_id: str):
        ready, error = await self._ensure_plugin_ready(wait=False)
        if error:
            return error
        return await self.external_handler.handle_get_memory(memory_id)

    async def external_archive_memory(self, memory_id: str):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        return await self.external_handler.handle_archive_memory(memory_id)

    # ==================== 辅助方法 ====================

    def _current_platform_names(self) -> set[str]:
        """Collect platform names used as the first segment of unified_msg_origin."""
        names: set[str] = set()
        context = getattr(self.plugin, "context", None)
        platform_manager = getattr(context, "platform_manager", None)
        get_insts = getattr(platform_manager, "get_insts", None)
        if not callable(get_insts):
            return names
        for instance in get_insts() or []:
            meta = getattr(instance, "meta", None)
            metadata = meta() if callable(meta) else None
            name = getattr(metadata, "name", None)
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
        return names

    async def _existing_session_ids(self, conversation_manager: Any) -> list[str]:
        """List known conversation sessions for import session-id remapping."""
        store = getattr(conversation_manager, "store", None)
        list_session_ids = getattr(store, "list_session_ids", None)
        if not callable(list_session_ids):
            return []
        return list(await list_session_ids())

    async def _ensure_plugin_ready(
        self, *, wait: bool = True
    ) -> tuple[dict[str, Any] | None, dict | None]:
        """确保插件已就绪。

        Args:
            wait: True 时等待后台初始化完成（写操作/显式重试）；False 时只做
                一次非阻塞检查，未就绪立即返回结构化 not_ready 错误，供读接口
                避免页面请求被初始化卡住 30 秒。

        Returns:
            (ready_dict, error_dict) 元组
            - ready_dict: 包含 memory_engine 等组件的字典
            - error_dict: 错误响应字典（如果有错误）
        """
        ready, message = await self.plugin._ensure_plugin_ready(wait=wait)
        if not ready:
            initializer = getattr(self.plugin, "initializer", None)
            is_failed = bool(getattr(initializer, "is_failed", False))
            return (
                None,
                self.utils.not_ready(
                    message or "插件尚未就绪",
                    init_state="failed" if is_failed else "in_progress",
                ),
            )

        return {
            "memory_engine": self.plugin.initializer.memory_engine,
            "conversation_manager": self.plugin.initializer.conversation_manager,
            "index_validator": self.plugin.initializer.index_validator,
            "memory_processor": getattr(
                self.plugin.initializer, "memory_processor", None
            ),
            "consolidation_manager": getattr(
                self.plugin.initializer, "consolidation_manager", None
            ),
            "config_manager": getattr(self.plugin, "config_manager", None)
            or getattr(self.plugin.initializer, "config_manager", None),
        }, None
