"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化和生命周期管理
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .core.base.config_manager import ConfigManager
from .core.command_handler import CommandHandler
from .core.event_handler import EventHandler
from .core.plugin_initializer import PluginInitializer
from .webui import WebUIServer


@register(
    "LivingMemory",
    "lxfight",
    "一个拥有动态生命周期的智能长期记忆插件。",
    "2.0.0",
    "https://github.com/lxfight/astrbot_plugin_livingmemory",
)
class LivingMemoryPlugin(Star):
    """LivingMemory 插件主类"""

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.context = context

        # 获取插件数据目录
        data_dir = str(StarTools.get_data_dir())

        # 初始化配置管理器
        self.config_manager = ConfigManager(config)

        # 初始化插件初始化器
        self.initializer = PluginInitializer(context, self.config_manager, data_dir)

        # 事件处理器和命令处理器（初始化后创建）
        self.event_handler: EventHandler | None = None
        self.command_handler: CommandHandler | None = None

        # WebUI 服务句柄
        self.webui_server: WebUIServer | None = None

        # 后台任务跟踪集合
        self._background_tasks: set[asyncio.Task] = set()

        # 启动非阻塞的初始化任务
        self._create_tracked_task(self._initialize_plugin())

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建并跟踪后台任务"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _initialize_plugin(self):
        """初始化插件"""
        try:
            # 执行初始化
            success = await self.initializer.initialize()

            # initialize() 可能先返回 False 并在后台重试完成；这里等待最终结果，
            # 避免出现 initializer 已完成但 command_handler/event_handler 仍为 None。
            if not success and not self.initializer.is_failed:
                logger.info("初始化进入后台重试，等待完成后绑定处理器...")
                success = await self.initializer.ensure_initialized(timeout=300.0)

            if not success:
                return

            if not self._bind_runtime_handlers():
                logger.error("插件初始化不完整：部分核心组件未能初始化")
                return

            # 启动 WebUI
            await self._start_webui()
            # 启动空闲自动总结巡检
            self._start_idle_summary_monitor()

        except Exception as e:
            logger.error(f"插件初始化失败: {e}", exc_info=True)

    def _bind_runtime_handlers(self) -> bool:
        """在 initializer 完成后绑定事件/命令处理器（幂等）。"""
        if not all(
            [
                self.initializer.memory_engine,
                self.initializer.memory_processor,
                self.initializer.conversation_manager,
            ]
        ):
            return False

        if not self.event_handler:
            self.event_handler = EventHandler(
                context=self.context,
                config_manager=self.config_manager,
                memory_engine=self.initializer.memory_engine,  # type: ignore[arg-type]
                memory_processor=self.initializer.memory_processor,  # type: ignore[arg-type]
                conversation_manager=self.initializer.conversation_manager,  # type: ignore[arg-type]
            )

        if not self.command_handler:
            self.command_handler = CommandHandler(
                context=self.context,
                config_manager=self.config_manager,
                memory_engine=self.initializer.memory_engine,
                conversation_manager=self.initializer.conversation_manager,
                index_validator=self.initializer.index_validator,
                webui_server=self.webui_server,
                initialization_status_callback=self._get_initialization_status_message,
            )

        return True

    async def _start_webui(self):
        """根据配置启动 WebUI 控制台"""
        webui_config = self.config_manager.webui_settings
        if not webui_config.get("enabled"):
            return
        if self.webui_server:
            return

        try:
            self.webui_server = WebUIServer(
                memory_engine=self.initializer.memory_engine,
                config=webui_config,
                conversation_manager=self.initializer.conversation_manager,
                index_validator=self.initializer.index_validator,
            )

            await self.webui_server.start()

            # 同步更新命令处理器中的 WebUI 句柄，避免 /lmem webui 误判未启用
            if self.command_handler:
                self.command_handler.webui_server = self.webui_server

            logger.info(
                f"🌐 WebUI 已启动: http://{webui_config.get('host', '127.0.0.1')}:{webui_config.get('port', 8080)}"
            )
        except Exception as e:
            logger.error(f"启动 WebUI 控制台失败: {e}", exc_info=True)
            self.webui_server = None

    async def _stop_webui(self):
        """停止 WebUI 控制台"""
        if not self.webui_server:
            return
        try:
            await self.webui_server.stop()
        except Exception as e:
            logger.warning(f"停止 WebUI 控制台时出现异常: {e}", exc_info=True)
        finally:
            if self.command_handler:
                self.command_handler.webui_server = None
            self.webui_server = None

    def _start_idle_summary_monitor(self):
        """按配置启动空闲自动总结后台巡检任务"""
        if not self.event_handler:
            return
        if not self.config_manager.get("reflection_engine.enable_idle_auto_summary", False):
            return
        self._create_tracked_task(self._idle_summary_loop())

    async def _idle_summary_loop(self):
        """周期性扫描空闲会话，触发自动总结。"""
        timeout_seconds = int(
            self.config_manager.get(
                "reflection_engine.idle_summary_timeout_seconds", 1800
            )
        )
        interval = max(30, min(300, timeout_seconds // 3 if timeout_seconds > 0 else 60))
        logger.info(f"[idle-summary] 自动总结巡检已启动，周期={interval}s")
        try:
            while True:
                if not self.event_handler:
                    await asyncio.sleep(interval)
                    continue
                await self.event_handler.run_idle_summary_check()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.debug("[idle-summary] 自动总结巡检已停止")
            raise

    def _get_initialization_status_message(self) -> str:
        """获取初始化状态的用户友好消息"""
        if self.initializer.is_initialized:
            return "✅ 插件已就绪"
        elif self.initializer.is_failed:
            return f"❌ 插件初始化失败: {self.initializer.error_message}\n\n请检查：\n1. Embedding Provider 是否已配置\n2. LLM Provider 是否可用\n3. 查看日志获取详细错误信息"
        else:
            return f"⏳ 插件正在后台初始化中...\n已尝试: {self.initializer._provider_check_attempts} 次\n\n如果长时间未完成，请检查：\n1. Embedding Provider 配置\n2. 其他插件是否阻塞了初始化流程"

    # ==================== 事件钩子 ====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """[事件钩子] 捕获所有群聊消息用于记忆存储"""
        if not self.initializer.is_initialized or not self.event_handler:
            return

        await self.event_handler.handle_all_group_messages(event)

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[事件钩子] 在 LLM 请求前，查询并注入长期记忆"""
        if not await self.initializer.ensure_initialized():
            logger.debug("插件未完成初始化，跳过记忆召回")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_recall(event, req)

    @filter.on_llm_response()
    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """[事件钩子] 在 LLM 响应后，检查是否需要进行反思和记忆存储"""
        if not await self.initializer.ensure_initialized():
            logger.debug("插件未完成初始化，跳过记忆反思")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_reflection(event, resp)

    # ==================== 命令处理 ====================

    @filter.command("lmem status", priority=10)
    async def status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示记忆系统状态"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return
        async for message in self.command_handler.handle_status(event):
            yield message

    @filter.command("lmem search", priority=10)
    @filter.permission_type(PermissionType.ADMIN)
    async def search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 搜索记忆"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_search(event, query, k):
            yield message

    @filter.command("lmem forget")
    @filter.permission_type(PermissionType.ADMIN)
    async def forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 删除指定记忆"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_forget(event, doc_id):
            yield message

    @filter.command("lmem rebuild-index")
    @filter.permission_type(PermissionType.ADMIN)
    async def rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 手动重建索引"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_rebuild_index(event):
            yield message

    @filter.command("lmem webui")
    @filter.permission_type(PermissionType.ADMIN)
    async def webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示WebUI访问信息"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_webui(event):
            yield message

    @filter.command("lmem reset")
    @filter.permission_type(PermissionType.ADMIN)
    async def reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 重置当前会话的长期记忆上下文"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_reset(event):
            yield message

    @filter.command("lmem pending")
    @filter.permission_type(PermissionType.ADMIN)
    async def pending(
        self, event: AstrMessageEvent, n: int = 0
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 查看当前会话待总结消息"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_pending(event, n):
            yield message

    @filter.command("lmem pending-del")
    @filter.permission_type(PermissionType.ADMIN)
    async def pending_del(
        self, event: AstrMessageEvent, round_no: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 删除待总结中的指定轮次"""
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_pending_del(event, round_no):
            yield message

    @filter.command("lmem cleanup")
    @filter.permission_type(PermissionType.ADMIN)
    async def cleanup(
        self, event: AstrMessageEvent, mode: str = "preview"
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 清理历史消息中的记忆注入片段

        Args:
            mode: 执行模式, "preview"(默认)为预演, "exec"为实际清理
        """
        if not await self.initializer.ensure_initialized():
            yield event.plain_result(self._get_initialization_status_message())
            return

        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        # 判断是否为执行模式
        dry_run = mode.lower() != "exec"

        async for message in self.command_handler.handle_cleanup(
            event, dry_run=dry_run
        ):
            yield message

    @filter.command("lmem help")
    @filter.permission_type(PermissionType.ADMIN)
    async def help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示帮助信息"""
        if not self.command_handler:
            yield event.plain_result("❌ 命令处理器未初始化")
            return

        async for message in self.command_handler.handle_help(event):
            yield message

    # ==================== 生命周期管理 ====================

    async def terminate(self):
        """插件停止时的清理逻辑"""
        logger.info("LivingMemory 插件正在停止...")

        # 取消所有后台任务
        if self._background_tasks:
            logger.info(f"正在取消 {len(self._background_tasks)} 个后台任务...")
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # 通知EventHandler停止（如果有正在运行的存储任务）
        if self.event_handler:
            await self.event_handler.shutdown()

        # 停止 WebUI
        await self._stop_webui()

        # 停止衰减调度器
        await self.initializer.stop_scheduler()

        # 关闭 ConversationManager
        if (
            self.initializer.conversation_manager
            and self.initializer.conversation_manager.store
        ):
            await self.initializer.conversation_manager.store.close()
            logger.info("✅ ConversationManager 已关闭")

        # 关闭 MemoryEngine
        if self.initializer.memory_engine:
            await self.initializer.memory_engine.close()
            logger.info("✅ MemoryEngine 已关闭")

        # 关闭 FaissVecDB
        if self.initializer.db:
            await self.initializer.db.close()
            logger.info("✅ FaissVecDB 已关闭")

        logger.info("LivingMemory 插件已成功停止。")
