"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化和生命周期管理
"""

import asyncio
import re
from collections.abc import AsyncGenerator
from importlib import metadata as importlib_metadata
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .core.base.config_manager import ConfigManager
from .core.command_handler import CommandHandler
from .core.event_handler import EventHandler
from .core.i18n_backend import init as i18n_init
from .core.i18n_backend import t
from .core.managers.backup_manager import BackupManager
from .core.passive_group_capture import PassiveGroupCaptureFilter
from .core.passive_group_capture import get_active_plugin
from .core.passive_group_capture import is_plugin_enabled_for_session
from .core.passive_group_capture import is_session_enabled
from .core.passive_group_capture import set_active_plugin
from .core.plugin_initializer import PluginInitializer
from .core.tools import MemoryMemorizeTool, MemorySearchTool

_MIN_ASTRBOT_VERSION = "4.24.2"
_ASTRBOT_DISTRIBUTION_NAMES = ("AstrBot", "astrbot")


def _parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", v.strip(), re.IGNORECASE)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(1).split("."))


def _version_lt(current: str, minimum: str) -> bool:
    current_parts = _parse_version(current)
    minimum_parts = _parse_version(minimum)
    if not current_parts or not minimum_parts:
        return False
    width = max(len(current_parts), len(minimum_parts))
    return current_parts + (0,) * (width - len(current_parts)) < minimum_parts + (
        0,
    ) * (width - len(minimum_parts))


def _detect_astrbot_version() -> str | None:
    for distribution_name in _ASTRBOT_DISTRIBUTION_NAMES:
        try:
            return importlib_metadata.version(distribution_name)
        except importlib_metadata.PackageNotFoundError:
            continue
        except Exception as exc:
            logger.debug(f"读取 AstrBot 分发版本失败 ({distribution_name}): {exc}")

    for module_name in ("astrbot.core.config.default", "astrbot.core.config"):
        try:
            module = __import__(module_name, fromlist=["VERSION"])
            version_value = getattr(module, "VERSION", None)
        except Exception as exc:
            logger.debug(f"读取 AstrBot 模块版本失败 ({module_name}): {exc}")
            continue
        if version_value:
            return str(version_value)

    return None


_CURRENT_ASTRBOT_VERSION = _detect_astrbot_version()

if _CURRENT_ASTRBOT_VERSION is None:
    logger.debug("未能检测到 AstrBot 版本，跳过 LivingMemory 版本兼容提示")
elif _version_lt(_CURRENT_ASTRBOT_VERSION, _MIN_ASTRBOT_VERSION):
    logger.warning(
        f"AstrBot 版本 {_CURRENT_ASTRBOT_VERSION} 低于推荐版本 {_MIN_ASTRBOT_VERSION}。"
        f"插件 Pages / WebUI 功能可能不可用。建议升级 AstrBot 以获得完整体验。"
    )


@register(
    "LivingMemory",
    "lxfight",
    "An intelligent long-term memory plugin with a dynamic lifecycle for AstrBot.",
    "2.3.6",
    "https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory",
)
class LivingMemoryPlugin(Star):
    """LivingMemory 插件主类"""

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.context = context

        # 获取插件数据目录
        data_dir = str(StarTools.get_data_dir("astrbot_plugin_livingmemory"))

        # 版本变更时自动备份数据（延迟到异步初始化阶段执行，避免 __init__ 中同步 I/O 阻塞）
        self._backup_manager = BackupManager(data_dir)

        # 初始化配置管理器
        self.config_manager = ConfigManager(config)

        # 初始化后端 i18n
        i18n_init(config.get("bot_language", "zh"))

        # 初始化插件初始化器
        self.initializer = PluginInitializer(context, self.config_manager, data_dir)

        # 事件处理器和命令处理器（初始化后创建）
        self.event_handler: EventHandler | None = None
        self.command_handler: CommandHandler | None = None

        # 后台任务跟踪集合
        self._background_tasks: set[asyncio.Task] = set()
        self._component_init_lock = asyncio.Lock()
        self._llm_tools_registered = False
        self._terminating = False

        self.page_api = None
        set_active_plugin(self)

        self._register_official_page_api_if_available()

        # 启动非阻塞的初始化任务
        self._create_tracked_task(self._initialize_plugin())

    def _register_official_page_api_if_available(self) -> None:
        """按需注册官方插件页面 API，避免旧版 AstrBot 因导入失败而无法加载插件。"""
        if not hasattr(self.context, "register_web_api"):
            return

        try:
            from .core.page_api import PluginPageApi
        except Exception as exc:
            logger.warning(
                f"官方插件页面 API 不可用，已跳过注册并保留旧版兼容模式: {exc}"
            )
            return

        try:
            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:
            self.page_api = None
            logger.warning(
                f"官方插件页面 API 注册失败，已跳过并保留旧版兼容模式: {exc}",
                exc_info=True,
            )

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建并跟踪后台任务"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _initialize_plugin(self):
        """初始化插件"""
        try:
            # 版本变更时自动备份数据（在任何数据库操作之前，通过线程池避免阻塞事件循环）
            await self._backup_manager.backup_if_needed_async()

            # 执行初始化
            success = await self.initializer.initialize()

            if success:
                await self._ensure_runtime_components()

        except Exception as e:
            logger.error(f"插件初始化失败: {e}", exc_info=True)

    async def _ensure_runtime_components(self) -> bool:
        """确保运行期组件（事件/命令处理器、WebUI）已就绪"""
        if self._terminating:
            return False
        if not self.initializer.is_initialized:
            return False

        async with self._component_init_lock:
            if self._terminating:
                return False
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
                    initialization_status_callback=self._get_initialization_status_message,
                )

            self._register_agent_tools_if_needed()

        return True

    def _register_agent_tools_if_needed(self) -> None:
        """在核心组件就绪后注册 Agent 工具（回忆/写入）。"""
        if self._llm_tools_registered:
            return
        if not self.initializer.memory_engine or not self.initializer.memory_processor:
            return

        tools = []
        if self.config_manager.get("agent_tools.enable_recall_tool", True):
            tools.append(
                MemorySearchTool(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,
                )
            )
        if self.config_manager.get("agent_tools.enable_memorize_tool", False):
            tools.append(
                MemoryMemorizeTool(
                    context=self.context,
                    memory_engine=self.initializer.memory_engine,
                    memory_processor=self.initializer.memory_processor,
                )
            )

        if tools:
            self.context.add_llm_tools(*tools)
        # 标记注册流程完成，后续不再重复检查。
        # 若用户中途修改 agent_tools 开关，需要重载插件才能生效。
        self._llm_tools_registered = True

    def _schedule_passive_group_capture(self, event: AstrMessageEvent) -> None:
        """Schedule full group capture from a filter without waking the message."""
        if self._terminating or not self.initializer.is_initialized:
            return
        self._create_tracked_task(self._run_passive_group_capture(event))

    async def _run_passive_group_capture(self, event: AstrMessageEvent) -> None:
        try:
            if not await is_session_enabled(event.unified_msg_origin):
                logger.debug(
                    f"[{event.unified_msg_origin}] 当前会话已关闭，"
                    "跳过被动群聊消息捕获"
                )
                return
            if not await is_plugin_enabled_for_session(event.unified_msg_origin):
                logger.debug(
                    f"[{event.unified_msg_origin}] LivingMemory 已在当前会话禁用，"
                    "跳过被动群聊消息捕获"
                )
                return
            if not await self._ensure_runtime_components():
                logger.debug("插件组件未就绪，跳过被动群聊消息捕获")
                return
            if not self.event_handler:
                return
            await self.event_handler.handle_all_group_messages(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"被动群聊消息捕获失败: {e}", exc_info=True)

    async def _ensure_plugin_ready(self) -> tuple[bool, str]:
        """确保插件已完成初始化并且运行期组件可用"""
        if not await self.initializer.ensure_initialized():
            return False, self._get_initialization_status_message()

        if not await self._ensure_runtime_components():
            return (
                False,
                t("command.core_not_ready"),
            )

        return True, ""

    def _get_initialization_status_message(self) -> str:
        """获取初始化状态的用户友好消息"""
        if self.initializer.is_initialized:
            return t("init.ready")
        elif self.initializer.is_failed:
            return t(
                "init.failed",
                error=self.initializer.error_message or t("common.unknown_error"),
            )
        else:
            return t(
                "init.in_progress",
                attempts=self.initializer._provider_check_attempts,
            )

    @staticmethod
    def _command_handler_not_ready_message() -> str:
        """命令处理器未就绪时的提示"""
        return t("command.not_ready")

    # ==================== 事件钩子 ====================

    @filter.custom_filter(PassiveGroupCaptureFilter, False)
    async def handle_all_group_messages(self, event: AstrMessageEvent):
        """[Passive Filter Hook] Capture group messages without waking AstrBot."""
        # PassiveGroupCaptureFilter schedules the capture task and always returns
        # False, so AstrBot will not invoke this handler or mark the event as wake.
        return

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[Event Hook] Query and inject long-term memory before LLM request"""
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
        """[Event Hook] Check if reflection and memory storage is needed after LLM response"""
        ready, _ = await self._ensure_plugin_ready()
        if not ready:
            logger.debug("插件未完成初始化，跳过记忆反思")
            return

        if not self.event_handler:
            return

        await self.event_handler.handle_memory_reflection(event, resp)

    @filter.after_message_sent()
    async def handle_session_reset(self, event: AstrMessageEvent, *_args):
        """[Event Hook] After message sent, check if plugin session context needs clearing (/reset or /new)"""
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
        """Long-term memory management command group /lmem"""
        pass

    @permission_type(PermissionType.ADMIN)
    @lmem.command("status", priority=10)
    async def status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[Admin] Show memory system status"""
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
        """[Admin] Search memories"""
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
        """[Admin] Delete specified memory"""
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
        """[Admin] Manually rebuild index"""
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
    @lmem.command("rebuild-graph")
    async def rebuild_graph(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[Admin] Manually rebuild graph memory index"""
        ready, message = await self._ensure_plugin_ready()
        if not ready:
            yield event.plain_result(message)
            return

        if not self.command_handler:
            yield event.plain_result(self._command_handler_not_ready_message())
            return

        async for message in self.command_handler.handle_rebuild_graph(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem.command("webui")
    async def webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """[Admin] Show WebUI access information"""
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
        """[Admin] Immediately trigger memory summarization for current session"""
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
        """[Admin] Reset long-term memory context for current session"""
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
        """[Admin] Clean up memory injection fragments from historical messages

        Args:
            mode: Execution mode, "preview" (default) for rehearsal, "exec" for actual cleanup
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
        """[Admin] Show help information"""
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
        """Cleanup logic when plugin stops"""
        logger.info("LivingMemory 插件正在停止...")
        self._terminating = True
        if get_active_plugin() is self:
            set_active_plugin(None)

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
