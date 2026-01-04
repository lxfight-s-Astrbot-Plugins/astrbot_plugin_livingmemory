"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化和生命周期管理
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
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

        # 启动非阻塞的初始化任务
        asyncio.create_task(self._initialize_plugin())

    async def _initialize_plugin(self):
        """初始化插件"""
        try:
            # 执行初始化
            success = await self.initializer.initialize()

            if success:
                # 检查必要组件是否初始化成功
                if not all(
                    [
                        self.initializer.memory_engine,
                        self.initializer.memory_processor,
                        self.initializer.conversation_manager,
                    ]
                ):
                    logger.error("插件初始化不完整：部分核心组件未能初始化")
                    return

                # 创建事件处理器
                self.event_handler = EventHandler(
                    context=self.context,
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,  # type: ignore[arg-type]
                    memory_processor=self.initializer.memory_processor,  # type: ignore[arg-type]
                    conversation_manager=self.initializer.conversation_manager,  # type: ignore[arg-type]
                )

                # 创建命令处理器
                self.command_handler = CommandHandler(
                    config_manager=self.config_manager,
                    memory_engine=self.initializer.memory_engine,
                    conversation_manager=self.initializer.conversation_manager,
                    index_validator=self.initializer.index_validator,
                    webui_server=self.webui_server,
                    initialization_status_callback=self._get_initialization_status_message,
                )

                # 启动 WebUI
                await self._start_webui()

        except Exception as e:
            logger.error(f"插件初始化失败: {e}", exc_info=True)

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
            self.webui_server = None

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

    @filter.command_group("lmem")
    def lmem_group(self):
        """长期记忆管理命令组 /lmem"""
        pass

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("status")
    async def lmem_status(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """[管理员] 显示记忆系统状态"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_status(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("search")
    async def lmem_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[str, None]:
        """[管理员] 搜索记忆"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_search(event, query, k):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("forget")
    async def lmem_forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[str, None]:
        """[管理员] 删除指定记忆"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_forget(event, doc_id):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("rebuild-index")
    async def lmem_rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[str, None]:
        """[管理员] 手动重建索引"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_rebuild_index(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("webui")
    async def lmem_webui(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """[管理员] 显示WebUI访问信息"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_webui(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("reset")
    async def lmem_reset(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """[管理员] 重置当前会话的长期记忆上下文"""
        if not await self.initializer.ensure_initialized():
            yield self._get_initialization_status_message()
            return

        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_reset(event):
            yield message

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("help")
    async def lmem_help(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """[管理员] 显示帮助信息"""
        if not self.command_handler:
            yield "❌ 命令处理器未初始化"
            return

        async for message in self.command_handler.handle_help(event):
            yield message

    # ==================== 生命周期管理 ====================

    async def terminate(self):
        """插件停止时的清理逻辑"""
        logger.info("LivingMemory 插件正在停止...")

        # 停止 WebUI
        await self._stop_webui()

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
