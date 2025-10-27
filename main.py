# -*- coding: utf-8 -*-
"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化MemoryEngine、绑定事件钩子以及管理生命周期。
简化版 - 只包含5个核心指令
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any

# AstrBot API
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.provider import LLMResponse, ProviderRequest, Provider
from astrbot.core.provider.provider import EmbeddingProvider
from astrbot.api import logger
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

# 插件内部模块
from .core.memory_engine import MemoryEngine
from .storage.db_migration import DBMigration
from .core.utils import (
    get_persona_id,
    format_memories_for_injection,
    retry_on_failure,
    OperationContext,
)
from .core.config_validator import validate_config, merge_config_with_defaults
from .webui import WebUIServer


# 会话管理器类
class SessionManager:
    def __init__(self, max_sessions: int = 1000, session_ttl: int = 3600):
        """
        Args:
            max_sessions: 最大会话数量
            session_ttl: 会话生存时间（秒）
        """
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._access_times: Dict[str, float] = {}
        self.max_sessions = max_sessions
        self.session_ttl = session_ttl

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """获取会话数据，如果不存在则创建"""
        current_time = time.time()
        self._cleanup_expired_sessions(current_time)

        if session_id not in self._sessions:
            self._sessions[session_id] = {"history": [], "round_count": 0}

        self._access_times[session_id] = current_time
        return self._sessions[session_id]

    def _cleanup_expired_sessions(self, current_time: float):
        """清理过期的会话"""
        expired_sessions = []
        for session_id, last_access in self._access_times.items():
            if current_time - last_access > self.session_ttl:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            self._sessions.pop(session_id, None)
            self._access_times.pop(session_id, None)

        if len(self._sessions) > self.max_sessions:
            sorted_sessions = sorted(self._access_times.items(), key=lambda x: x[1])
            sessions_to_remove = sorted_sessions[
                : len(self._sessions) - self.max_sessions
            ]

            for session_id, _ in sessions_to_remove:
                self._sessions.pop(session_id, None)
                self._access_times.pop(session_id, None)

    def reset_session(self, session_id: str):
        """重置指定会话"""
        if session_id in self._sessions:
            self._sessions[session_id] = {"history": [], "round_count": 0}
            self._access_times[session_id] = time.time()

    def get_session_count(self) -> int:
        """获取当前会话数量"""
        return len(self._sessions)


@register(
    "LivingMemory",
    "lxfight",
    "一个拥有动态生命周期的智能长期记忆插件。",
    "2.0.0",
    "https://github.com/lxfight/astrbot_plugin_livingmemory",
)
class LivingMemoryPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.context = context

        # 验证和标准化配置
        try:
            merged_config = merge_config_with_defaults(config)
            self.config_obj = validate_config(merged_config)
            self.config = self.config_obj.model_dump()
            logger.info("插件配置验证成功")
        except Exception as e:
            logger.error(f"配置验证失败，使用默认配置: {e}")
            from .core.config_validator import get_default_config

            self.config = get_default_config()
            self.config_obj = validate_config(self.config)

        # 初始化状态
        self.embedding_provider: Optional[EmbeddingProvider] = None
        self.llm_provider: Optional[Provider] = None
        self.db: Optional[FaissVecDB] = None
        self.memory_engine: Optional[MemoryEngine] = None
        self.db_migration: Optional[DBMigration] = None

        # 初始化状态标记
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()

        # 会话管理器
        session_config = self.config.get("session_manager", {})
        self.session_manager = SessionManager(
            max_sessions=session_config.get("max_sessions", 1000),
            session_ttl=session_config.get("session_ttl", 3600),
        )

        # WebUI 服务句柄
        self.webui_server: Optional[WebUIServer] = None

        # 启动初始化任务
        asyncio.create_task(self._initialize_plugin())

    async def _initialize_plugin(self):
        """执行插件的异步初始化"""
        async with self._initialization_lock:
            if self._initialization_complete:
                return

        logger.info("开始初始化 LivingMemory 插件...")
        try:
            # 1. 初始化 Provider
            self._initialize_providers()
            if not self.embedding_provider or not self.llm_provider:
                logger.error("Provider 初始化失败，插件无法正常工作。")
                return

            # 2. 初始化数据库
            data_dir = StarTools.get_data_dir()
            db_path = os.path.join(data_dir, "livingmemory.db")
            index_path = os.path.join(data_dir, "livingmemory.index")
            self.db = FaissVecDB(db_path, index_path, self.embedding_provider)
            await self.db.initialize()
            logger.info(f"数据库已初始化。数据目录: {data_dir}")

            # 3. 初始化数据库迁移管理器
            self.db_migration = DBMigration(db_path)

            # 4. 检查并执行数据库迁移
            migration_config = self.config.get("migration_settings", {})
            if migration_config.get("auto_migrate", True):
                await self._check_and_migrate_database()

            # 5. 初始化MemoryEngine（新的统一记忆引擎）
            memory_engine_config = {
                "rrf_k": self.config.get("fusion_strategy", {}).get("rrf_k", 60),
                "decay_rate": self.config.get("importance_decay", {}).get(
                    "decay_rate", 0.01
                ),
                "importance_weight": self.config.get("recall_engine", {}).get(
                    "importance_weight", 1.0
                ),
                "fallback_enabled": self.config.get("recall_engine", {}).get(
                    "fallback_to_vector", True
                ),
                "cleanup_days_threshold": self.config.get("forgetting_agent", {}).get(
                    "cleanup_days_threshold", 30
                ),
                "cleanup_importance_threshold": self.config.get(
                    "forgetting_agent", {}
                ).get("cleanup_importance_threshold", 0.3),
            }

            self.memory_engine = MemoryEngine(
                db_path=db_path,
                faiss_db=self.db,
                llm_provider=self.llm_provider,
                config=memory_engine_config,
            )
            await self.memory_engine.initialize()
            logger.info("✅ MemoryEngine 已初始化")

            # 6. 启动 WebUI（如启用）
            await self._start_webui()

            # 标记初始化完成
            self._initialization_complete = True
            logger.info("LivingMemory 插件初始化成功！")

        except Exception as e:
            logger.critical(
                f"LivingMemory 插件初始化过程中发生严重错误: {e}", exc_info=True
            )
            self._initialization_complete = False

    async def _check_and_migrate_database(self):
        """检查并执行数据库迁移"""
        try:
            if not self.db_migration:
                logger.warning("数据库迁移管理器未初始化")
                return

            needs_migration = await self.db_migration.needs_migration()

            if not needs_migration:
                logger.info("✅ 数据库版本已是最新，无需迁移")
                return

            logger.info("🔄 检测到旧版本数据库，开始自动迁移...")

            migration_config = self.config.get("migration_settings", {})

            if migration_config.get("create_backup", True):
                backup_path = await self.db_migration.create_backup()
                if backup_path:
                    logger.info(f"✅ 数据库备份已创建: {backup_path}")
                else:
                    logger.warning("⚠️ 数据库备份失败，但将继续迁移")

            result = await self.db_migration.migrate(
                sparse_retriever=None, progress_callback=None
            )

            if result.get("success"):
                logger.info(f"✅ {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
            else:
                logger.error(f"❌ 数据库迁移失败: {result.get('message')}")

        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _start_webui(self):
        """根据配置启动 WebUI 控制台"""
        webui_config = self.config.get("webui_settings", {})
        if not webui_config.get("enabled"):
            return
        if self.webui_server:
            return

        try:
            # 导入WebUI服务器
            from .webui.server import WebUIServer
            
            # 创建WebUI服务器实例
            self.webui_server = WebUIServer(
                memory_engine=self.memory_engine,
                config=webui_config
            )
            
            # 启动WebUI服务器
            await self.webui_server.start()
            
            logger.info(f"✅ WebUI 已启动: http://{webui_config.get('host', '127.0.0.1')}:{webui_config.get('port', 8080)}")
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

    async def _wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """等待插件初始化完成"""
        if self._initialization_complete:
            return True

        start_time = time.time()
        while not self._initialization_complete:
            if time.time() - start_time > timeout:
                logger.error(f"插件初始化超时（{timeout}秒）")
                return False
            await asyncio.sleep(0.1)

        return self._initialization_complete

    def _get_webui_url(self) -> Optional[str]:
        """获取 WebUI 访问地址"""
        webui_config = self.config.get("webui_settings", {})
        if not webui_config.get("enabled") or not self.webui_server:
            return None

        host = webui_config.get("host", "127.0.0.1")
        port = webui_config.get("port", 8080)

        if host in ["0.0.0.0", ""]:
            return f"http://127.0.0.1:{port}"
        else:
            return f"http://{host}:{port}"

    def _initialize_providers(self):
        """初始化 Embedding 和 LLM provider"""
        # 初始化 Embedding Provider
        emb_id = self.config.get("provider_settings", {}).get("embedding_provider_id")
        if emb_id:
            self.embedding_provider = self.context.get_provider_by_id(emb_id)
            if self.embedding_provider:
                logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")

        if not self.embedding_provider:
            embedding_providers = self.context.provider_manager.embedding_provider_insts
            if embedding_providers:
                self.embedding_provider = embedding_providers[0]
                logger.info(
                    f"未指定 Embedding Provider，使用默认的: {self.embedding_provider.provider_config.get('id')}"
                )
            else:
                self.embedding_provider = None
                logger.error("没有可用的 Embedding Provider，插件将无法使用。")

        # 初始化 LLM Provider
        llm_id = self.config.get("provider_settings", {}).get("llm_provider_id")
        if llm_id:
            self.llm_provider = self.context.get_provider_by_id(llm_id)
            if self.llm_provider:
                logger.info(f"成功从配置加载 LLM Provider: {llm_id}")
        else:
            self.llm_provider = self.context.get_using_provider()
            logger.info("使用 AstrBot 当前默认的 LLM Provider。")

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[事件钩子] 在 LLM 请求前，查询并注入长期记忆"""
        if not await self._wait_for_initialization():
            logger.warning("插件未完成初始化，跳过记忆召回。")
            return

        if not self.memory_engine:
            logger.debug("记忆引擎尚未初始化，跳过记忆召回。")
            return

        try:
            session_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )

            async with OperationContext("记忆召回", session_id):
                # 根据配置决定是否进行过滤
                filtering_config = self.config.get("filtering_settings", {})
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                persona_id = await get_persona_id(self.context, event)

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                # 使用 MemoryEngine 进行智能回忆
                recalled_memories = await self.memory_engine.search_memories(
                    query=req.prompt,
                    k=self.config.get("recall_engine", {}).get("top_k", 5),
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                )

                if recalled_memories:
                    # 格式化并注入记忆
                    memory_list = [
                        {
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": {
                                "importance": mem.metadata.get("importance", 0.5)
                            },
                        }
                        for mem in recalled_memories
                    ]
                    memory_str = format_memories_for_injection(memory_list)
                    req.system_prompt = memory_str + "\n" + req.system_prompt
                    logger.info(
                        f"[{session_id}] 成功向 System Prompt 注入 {len(recalled_memories)} 条记忆。"
                    )

                # 管理会话历史
                session_data = self.session_manager.get_session(session_id)
                session_data["history"].append({"role": "user", "content": req.prompt})

        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)

    @filter.on_llm_response()
    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """[事件钩子] 在 LLM 响应后，检查是否需要进行反思和记忆存储"""
        if not await self._wait_for_initialization():
            logger.warning("插件未完成初始化，跳过记忆反思。")
            return

        if not self.memory_engine or resp.role != "assistant":
            logger.debug("记忆引擎尚未初始化或响应不是助手角色，跳过反思。")
            return

        try:
            session_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            if not session_id:
                return

            # 添加助手响应到历史并增加轮次计数
            current_session = self.session_manager.get_session(session_id)
            current_session["history"].append(
                {"role": "assistant", "content": resp.completion_text}
            )
            current_session["round_count"] += 1

            # 检查是否满足总结条件
            trigger_rounds = self.config.get("reflection_engine", {}).get(
                "summary_trigger_rounds", 10
            )
            logger.debug(
                f"[{session_id}] 当前轮次: {current_session['round_count']}, 触发轮次: {trigger_rounds}"
            )

            if current_session["round_count"] >= trigger_rounds:
                logger.info(
                    f"[{session_id}] 对话达到 {trigger_rounds} 轮，启动反思任务。"
                )

                history_to_reflect = list(current_session["history"])
                # 重置会话
                self.session_manager.reset_session(session_id)

                persona_id = await get_persona_id(self.context, event)

                # 创建后台任务进行存储(简化版,直接存储对话摘要)
                async def storage_task():
                    async with OperationContext("记忆存储", session_id):
                        try:
                            # 将对话历史合并为文本
                            conversation_text = "\n".join(
                                [
                                    f"{msg['role']}: {msg['content']}"
                                    for msg in history_to_reflect
                                ]
                            )

                            # 添加到记忆引擎
                            await self.memory_engine.add_memory(
                                content=conversation_text,
                                session_id=session_id,
                                persona_id=persona_id,
                                importance=0.7,  # 默认重要性
                            )
                            logger.info(f"[{session_id}] 成功存储对话记忆")
                        except Exception as e:
                            logger.error(
                                f"[{session_id}] 存储记忆失败: {e}", exc_info=True
                            )

                asyncio.create_task(storage_task())

        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)

    # --- 命令处理 ---
    @filter.command_group("lmem")
    def lmem_group(self):
        """长期记忆管理命令组 /lmem"""
        pass

    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """从event获取session_id的辅助方法"""
        try:
            loop = asyncio.get_event_loop()
            session_id = loop.run_until_complete(
                self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            return session_id or "default"
        except Exception as e:
            logger.error(f"获取会话ID失败: {e}", exc_info=True)
            return "default"

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("status")
    async def lmem_status(self, event: AstrMessageEvent):
        """[管理员] 显示记忆系统状态"""
        if not await self._wait_for_initialization():
            yield event.plain_result("插件尚未完成初始化，请稍后再试。")
            return

        if not self.memory_engine:
            yield event.plain_result("❌ 记忆引擎未初始化")
            return

        try:
            stats = await self.memory_engine.get_statistics()

            # 格式化时间
            last_update = "从未"
            if stats.get("newest_memory"):
                last_update = datetime.fromtimestamp(stats["newest_memory"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            # 计算数据库大小
            db_size = 0
            if os.path.exists(self.memory_engine.db_path):
                db_size = os.path.getsize(self.memory_engine.db_path) / (1024 * 1024)

            session_count = len(stats.get("sessions", {}))

            message = f"""📊 LivingMemory 状态报告

🔢 总记忆数: {stats["total_memories"]}
👥 会话数: {session_count}
⏰ 最后更新: {last_update}
💾 数据库: {db_size:.2f} MB

使用 /lmem search <关键词> 搜索记忆
使用 /lmem webui 访问管理界面"""

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 获取状态失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("search")
    async def lmem_search(self, event: AstrMessageEvent, query: str, k: int = 5):
        """[管理员] 搜索记忆"""
        if not await self._wait_for_initialization():
            yield event.plain_result("插件尚未完成初始化，请稍后再试。")
            return

        if not self.memory_engine:
            yield event.plain_result("❌ 记忆引擎未初始化")
            return

        try:
            session_id = self._get_session_id(event)
            results = await self.memory_engine.search_memories(
                query=query, k=k, session_id=session_id
            )

            if not results:
                yield event.plain_result(f"🔍 未找到与 '{query}' 相关的记忆")
                return

            message = f"🔍 找到 {len(results)} 条相关记忆:\n\n"
            for i, result in enumerate(results, 1):
                score = result.final_score
                content = (
                    result.content[:100] + "..."
                    if len(result.content) > 100
                    else result.content
                )
                message += f"{i}. [得分:{score:.2f}] {content}\n"
                message += f"   ID: {result.doc_id}\n\n"

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜索失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("forget")
    async def lmem_forget(self, event: AstrMessageEvent, doc_id: int):
        """[管理员] 删除指定记忆"""
        if not await self._wait_for_initialization():
            yield event.plain_result("插件尚未完成初始化，请稍后再试。")
            return

        if not self.memory_engine:
            yield event.plain_result("❌ 记忆引擎未初始化")
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                yield event.plain_result(f"✅ 已删除记忆 #{doc_id}")
            else:
                yield event.plain_result(f"❌ 删除失败，记忆 #{doc_id} 不存在")
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 删除失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("webui")
    async def lmem_webui(self, event: AstrMessageEvent):
        """[管理员] 显示WebUI访问信息"""
        if not await self._wait_for_initialization():
            yield event.plain_result("插件尚未完成初始化，请稍后再试。")
            return

        webui_url = self._get_webui_url()

        if not webui_url:
            message = """⚠️ WebUI 功能暂未启用

🚧 WebUI 正在适配新的 MemoryEngine 架构
📝 预计在下一个版本中恢复

💡 当前可用功能:
• /lmem status - 查看系统状态
• /lmem search - 搜索记忆
• /lmem forget - 删除记忆"""
        else:
            message = f"""🌐 LivingMemory WebUI

访问地址: {webui_url}

💡 WebUI功能:
• 📝 记忆编辑与管理
• 📊 可视化统计分析
• ⚙️ 高级配置管理
• 🔧 系统调试工具
• 💾 数据迁移管理

在WebUI中可以进行更复杂的操作!"""

        yield event.plain_result(message)

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("help")
    async def lmem_help(self, event: AstrMessageEvent):
        """[管理员] 显示帮助信息"""
        message = """📖 LivingMemory 使用指南

🔹 核心指令:
/lmem status              查看系统状态
/lmem search <关键词> [数量]  搜索记忆(默认5条)
/lmem forget <ID>          删除指定记忆
/lmem webui               打开WebUI管理界面
/lmem help                显示此帮助

💡 使用建议:
• 日常查询使用 search 指令
• 复杂管理使用 WebUI 界面
• 记忆会自动保存对话内容
• 使用 forget 删除敏感信息

📚 更多信息: https://github.com/lxfight/astrbot_plugin_livingmemory"""

        yield event.plain_result(message)

    async def terminate(self):
        """插件停止时的清理逻辑"""
        logger.info("LivingMemory 插件正在停止...")
        await self._stop_webui()
        if self.memory_engine:
            await self.memory_engine.close()
        if self.db:
            await self.db.close()
        logger.info("LivingMemory 插件已成功停止。")
