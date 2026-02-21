"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化和生命周期管理
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType, permission_type
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
    "https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory",
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
        self._component_init_lock = asyncio.Lock()

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

            if success:
                await self._ensure_runtime_components()

        except Exception as e:
            logger.error(f"插件初始化失败: {e}", exc_info=True)

    async def _ensure_runtime_components(self) -> bool:
        """确保运行期组件（事件/命令处理器、WebUI）已就绪"""
        if not self.initializer.is_initialized:
            return False

        async with self._component_init_lock:
            # 检查必要组件是否初始化成功
            if not all(
                [
                    self.initializer.memory_engine,
                    self.initializer.memory_processor,
                    self.initializer.conversation_manager,
                ]
            ):
                logger.error("插件初始化不完整：部分核心组件未能初始化")
                return False

            # 创建事件处理器（幂等）
            if not self.event_handler:
                self.event_handler = EventHandler(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,  # type: ignore[arg-type]
                    memory_processor=self.initializer.memory_processor,  # type: ignore[arg-type]
                    conversation_manager=self.initializer.conversation_manager,  # type: ignore[arg-type]
                )

            # 创建命令处理器（幂等）
            if not self.command_handler:
                self.command_handler = CommandHandler(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,
                    conversation_manager=self.initializer.conversation_manager,
                    index_validator=self.initializer.index_validator,
                    memory_processor=self.initializer.memory_processor,
                    webui_server=self.webui_server,
                    initialization_status_callback=self._get_initialization_status_message,
                )

            # 启动 WebUI（幂等）
            await self._start_webui()
            if self.command_handler:
                self.command_handler.webui_server = self.webui_server

        return True

    async def _ensure_plugin_ready(self) -> tuple[bool, str]:
        """确保插件已完成初始化并且运行期组件可用"""
        if not await self.initializer.ensure_initialized():
            return False, self._get_initialization_status_message()

        if not await self._ensure_runtime_components():
            return (
                False,
                "插件核心组件未初始化。\n"
                "请先执行 /lmem status 查看初始化状态；如仍失败，请检查启动日志中的异常堆栈。",
            )

        return True, ""

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
            if self.command_handler:
                self.command_handler.webui_server = self.webui_server

            logger.info(
                f"WebUI started at: http://{webui_config.get('host', '127.0.0.1')}:{webui_config.get('port', 8080)}"
            )
        except Exception as e:
            logger.error(f"启动 WebUI 控制台失败: {e}", exc_info=True)
            self.webui_server = None
            if self.command_handler:
                self.command_handler.webui_server = None

    async def _stop_webui(self):
        """停止 WebUI 控制台"""
        if not self.webui_server:
            return
        try:
            await self.webui_server.stop()
        except Exception as e:
            logger.warning(f"停止 WebUI 控制台时出现异常: {e}", exc_info=True)
        finally:
            self.webui_server = None
            if self.command_handler:
                self.command_handler.webui_server = None

    def _get_initialization_status_message(self) -> str:
        """获取初始化状态的用户友好消息"""
        if self.initializer.is_initialized:
            return "插件已就绪。可使用 /lmem help 查看可用命令。"
        elif self.initializer.is_failed:
            return (
                "插件初始化失败。\n"
                f"错误详情: {self.initializer.error_message or '未知错误'}\n\n"
                "请检查:\n"
                "1. Embedding Provider 是否已正确配置并可调用\n"
                "2. LLM Provider 是否可用\n"
                "3. 插件数据目录是否有读写权限\n"
                "4. 启动日志中的异常堆栈信息"
            )
        else:
            return (
                "插件正在后台初始化中。\n"
                f"当前已尝试检查 Provider: {self.initializer._provider_check_attempts} 次\n\n"
                "如果长时间未完成，请检查:\n"
                "1. Embedding Provider 与 LLM Provider 配置\n"
                "2. 其他插件是否阻塞初始化流程\n"
                "3. 日志中是否出现 Provider 相关报错"
            )

    @staticmethod
    def _command_handler_not_ready_message() -> str:
        """命令处理器未就绪时的提示"""
        return (
            "命令处理器尚未就绪，当前命令无法执行。\n"
            "请先执行 /lmem status 查看插件状态；若持续失败，请检查初始化日志。"
        )

    # ==================== 事件钩子 ====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """[事件钩子] 捕获所有群聊消息用于记忆存储"""
        if not self.initializer.is_initialized:
            return

        if not await self._ensure_runtime_components():
            logger.debug("插件组件未就绪，跳过群聊消息捕获")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_all_group_messages(event)

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[事件钩子] 在 LLM 请求前，查询并注入长期记忆"""
        ready, _ = await self._ensure_plugin_ready()
        if not ready:
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
        ready, _ = await self._ensure_plugin_ready()
        if not ready:
            logger.debug("插件未完成初始化，跳过记忆反思")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_reflection(event, resp)

    @filter.after_message_sent()
    async def handle_session_reset(self, event: AstrMessageEvent):
        """[事件钩子] 消息发送后，检查是否需要清空插件会话上下文（/reset 或 /new）"""
        if not event.get_extra("_clean_ltm_session", False):
            return

        ready, _ = await self._ensure_plugin_ready()
        if not ready:
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_session_reset(event)

    # ==================== 命令处理 ====================

    @filter.command_group("lmem")
    def lmem(self):
        """长期记忆管理命令组 /lmem"""
        pass

    @permission_type(PermissionType.ADMIN)
    @lmem.command("status", priority=10)
    async def status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示记忆系统状态"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return
        async for message in self.command_handler.handle_status(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("search", priority=10)
    async def search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 搜索记忆"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_search(event, query, k):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("forget")
    async def forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 删除指定记忆"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_forget(event, doc_id):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("rebuild-index")
    async def rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 手动重建索引"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_rebuild_index(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("webui")
    async def webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示WebUI访问信息"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_webui(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("summarize")
    async def summarize(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 立即触发当前会话的记忆总结"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_summarize(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("reset")
    async def reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 重置当前会话的长期记忆上下文"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_reset(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("cleanup")
    async def cleanup(
        self, event: AstrMessageEvent, mode: str = "preview"
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 清理历史消息中的记忆注入片段

        Args:
            mode: 执行模式, "preview"(默认)为预演, "exec"为实际清理
        """
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        # 判断是否为执行模式
        dry_run = mode.lower() != "exec"

        async for message in self.command_handler.handle_cleanup(
            event, dry_run=dry_run
        ):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("help")
    async def help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[管理员] 显示帮助信息"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
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

        # 停止初始化后台任务（如Provider重试）
        await self.initializer.stop_background_tasks()

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
            logger.info("ConversationManager 已关闭")

        # 关闭 MemoryEngine
        if self.initializer.memory_engine:
            await self.initializer.memory_engine.close()
            logger.info("MemoryEngine 已关闭")

        # 关闭 FaissVecDB
        if self.initializer.db:
            await self.initializer.db.close()
            logger.info("FaissVecDB 已关闭")

        logger.info("LivingMemory 插件已成功停止。")
