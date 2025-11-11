"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化MemoryEngine、绑定事件钩子以及管理生命周期。
简化版 - 只包含5个核心指令
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.provider import LLMResponse, Provider, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot.core.provider.provider import EmbeddingProvider

from .core.config_validator import merge_config_with_defaults, validate_config
from .core.conversation_manager import ConversationManager
from .core.index_validator import IndexValidator
from .core.memory_engine import MemoryEngine
from .core.memory_processor import MemoryProcessor
from .core.message_utils import store_round_with_length_check
from .core.utils import (
    OperationContext,
    format_memories_for_injection,
    get_persona_id,
)
from .storage.conversation_store import ConversationStore
from .storage.db_migration import DBMigration
from .webui import WebUIServer


@register(
    "LivingMemory",
    "lxfight",
    "一个拥有动态生命周期的智能长期记忆插件。",
    "1.6.6",
    "https://github.com/lxfight/astrbot_plugin_livingmemory",
)
class LivingMemoryPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any]):
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
        self.embedding_provider: EmbeddingProvider | None = None
        self.llm_provider: Provider | None = None
        self.db: FaissVecDB | None = None
        self.memory_engine: MemoryEngine | None = None
        self.memory_processor: MemoryProcessor | None = None
        self.db_migration: DBMigration | None = None
        self.conversation_manager: ConversationManager | None = None
        self.index_validator: IndexValidator | None = None

        # 初始化状态标记
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None

        # Provider 就绪标记
        self._providers_ready = False
        self._provider_check_attempts = 0
        self._max_provider_attempts = 60  # 最多尝试60次（60秒）

        # WebUI 服务句柄
        self.webui_server: WebUIServer | None = None

        # 启动非阻塞的初始化任务
        asyncio.create_task(self._initialize_plugin_async())

    async def _initialize_plugin_async(self):
        """非阻塞的异步初始化 - 后台持续尝试直到成功"""
        async with self._initialization_lock:
            if self._initialization_complete or self._initialization_failed:
                return

        logger.info("LivingMemory 插件开始后台初始化...")

        try:
            # 1. 非阻塞地等待 Provider 就绪
            if not await self._wait_for_providers_non_blocking():
                logger.warning("Provider 暂时不可用，将在后台继续尝试...")
                # 启动后台重试任务
                asyncio.create_task(self._retry_initialization())
                return

            # 2. Provider 就绪，继续完整初始化
            await self._complete_initialization()

        except Exception as e:
            logger.error(f"LivingMemory 插件初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)

    async def _wait_for_providers_non_blocking(self, max_wait: float = 5.0) -> bool:
        """非阻塞地检查 Provider 是否可用（最多等待几秒）"""
        start_time = time.time()
        check_interval = 1.0  # 每1秒检查一次（减少轮询频率）

        while time.time() - start_time < max_wait:
            self._initialize_providers(silent=True)  # 静默模式

            if self.embedding_provider and self.llm_provider:
                logger.info("✅ Provider 已就绪")
                self._providers_ready = True
                return True

            await asyncio.sleep(check_interval)
            self._provider_check_attempts += 1

        logger.debug(
            f"Provider 在 {max_wait}秒内未就绪（已尝试 {self._provider_check_attempts} 次）"
        )
        return False

    async def _retry_initialization(self):
        """后台重试初始化任务"""
        retry_interval = 2.0  # 每2秒重试一次
        log_interval = 5  # 每5次尝试输出一次日志

        while (
            not self._initialization_complete
            and not self._initialization_failed
            and self._provider_check_attempts < self._max_provider_attempts
        ):
            await asyncio.sleep(retry_interval)

            # 尝试获取 Provider（静默模式）
            self._initialize_providers(silent=True)
            self._provider_check_attempts += 1

            # 每5次尝试输出一次等待日志
            if self._provider_check_attempts % log_interval == 0:
                logger.info(
                    f"⏳ 等待 Provider 就绪中...（已尝试 {self._provider_check_attempts}/{self._max_provider_attempts} 次）"
                )

            if self.embedding_provider and self.llm_provider:
                logger.info(
                    f"✅ Provider 在第 {self._provider_check_attempts} 次尝试后就绪，继续初始化..."
                )
                self._providers_ready = True

                try:
                    async with self._initialization_lock:
                        if not self._initialization_complete:
                            await self._complete_initialization()
                except Exception as e:
                    logger.error(f"重试初始化失败: {e}", exc_info=True)
                    self._initialization_failed = True
                    self._initialization_error = str(e)
                break

        if not self._initialization_complete and not self._initialization_failed:
            logger.error(
                f"❌ Provider 在 {self._provider_check_attempts} 次尝试后仍未就绪，初始化失败"
            )
            logger.error(
                "💡 请检查：\n"
                "   1. 是否已配置 Embedding Provider（如 text-embedding-3-small）\n"
                "   2. Provider 配置是否正确\n"
                "   3. 其他插件是否占用了 Provider 资源"
            )
            self._initialization_failed = True
            self._initialization_error = "Provider 初始化超时"

    async def _complete_initialization(self):
        """完成完整的初始化流程（Provider 已就绪时调用）"""
        if self._initialization_complete:
            return

        logger.info("开始完整初始化流程...")

        try:
            # 2. 初始化数据库
            data_dir = StarTools.get_data_dir()
            db_path = os.path.join(data_dir, "livingmemory.db")
            index_path = os.path.join(data_dir, "livingmemory.index")

            # 确保 embedding_provider 不为 None
            if not self.embedding_provider:
                raise ValueError("Embedding Provider 未初始化，无法创建数据库")

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
            # 创建停用词目录
            stopwords_dir = os.path.join(data_dir, "stopwords")
            os.makedirs(stopwords_dir, exist_ok=True)

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
                "stopwords_path": stopwords_dir,  # 传递停用词目录
            }

            self.memory_engine = MemoryEngine(
                db_path=db_path,
                faiss_db=self.db,
                llm_provider=self.llm_provider,
                config=memory_engine_config,
            )
            await self.memory_engine.initialize()
            logger.info(" MemoryEngine 已初始化")

            # 6. 初始化 ConversationManager（高级会话管理器）
            conversation_db_path = os.path.join(data_dir, "conversations.db")
            conversation_store = ConversationStore(conversation_db_path)
            await conversation_store.initialize()

            session_config = self.config.get("session_manager", {})
            self.conversation_manager = ConversationManager(
                store=conversation_store,
                max_cache_size=session_config.get("max_sessions", 100),
                context_window_size=session_config.get("context_window_size", 50),
                session_ttl=session_config.get("session_ttl", 3600),
            )
            logger.info(" ConversationManager 已初始化")

            # 6.6. 初始化 MemoryProcessor（记忆处理器）
            if not self.llm_provider:
                raise ValueError("LLM Provider 未初始化，无法创建 MemoryProcessor")
            self.memory_processor = MemoryProcessor(self.llm_provider)
            logger.info(" MemoryProcessor 已初始化")

            # 6.7. 初始化索引验证器并自动重建索引
            self.index_validator = IndexValidator(db_path, self.db)
            await self._auto_rebuild_index_if_needed()

            # 6.5. 异步初始化 TextProcessor（加载停用词）
            if (
                self.memory_engine
                and hasattr(self.memory_engine, "text_processor")
                and self.memory_engine.text_processor is not None
                and hasattr(self.memory_engine.text_processor, "async_init")
            ):
                await self.memory_engine.text_processor.async_init()
                logger.info(" TextProcessor 停用词已加载")

            # 7. 启动 WebUI（如启用）
            await self._start_webui()

            # 标记初始化完成
            self._initialization_complete = True
            logger.info("✅ LivingMemory 插件初始化成功！")

        except Exception as e:
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            raise

    async def _check_and_migrate_database(self):
        """检查并执行数据库迁移"""
        try:
            if not self.db_migration:
                logger.warning("数据库迁移管理器未初始化")
                return

            needs_migration = await self.db_migration.needs_migration()

            if not needs_migration:
                logger.info(" 数据库版本已是最新，无需迁移")
                return

            logger.info(" 检测到旧版本数据库，开始自动迁移...")

            migration_config = self.config.get("migration_settings", {})

            if migration_config.get("create_backup", True):
                backup_path = await self.db_migration.create_backup()
                if backup_path:
                    logger.info(f" 数据库备份已创建: {backup_path}")
                else:
                    logger.warning("️ 数据库备份失败，但将继续迁移")

            result = await self.db_migration.migrate(
                sparse_retriever=None, progress_callback=None
            )

            if result.get("success"):
                logger.info(f" {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
            else:
                logger.error(f" 数据库迁移失败: {result.get('message')}")

        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _auto_rebuild_index_if_needed(self):
        """自动检查并重建索引（如果需要）"""
        try:
            if not self.index_validator or not self.memory_engine:
                return

            # 1. 检查v1迁移状态
            (
                needs_migration_rebuild,
                pending_count,
            ) = await self.index_validator.get_migration_status()

            if needs_migration_rebuild:
                logger.info(
                    f" 检测到 v1 迁移数据需要重建索引（{pending_count} 条文档）"
                )
                logger.info(" 开始自动重建索引...")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f" 索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f" 索引自动重建失败: {result.get('message')}")
                return

            # 2. 检查索引一致性
            status = await self.index_validator.check_consistency()

            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"️ 检测到索引不一致: {status.reason}")
                logger.info(
                    f" Documents: {status.documents_count}, BM25: {status.bm25_count}, Vector: {status.vector_count}"
                )
                logger.info(" 开始自动重建索引...")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f" 索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f" 索引自动重建失败: {result.get('message')}")
            else:
                logger.info(f" 索引一致性检查通过: {status.reason}")

        except Exception as e:
            logger.error(f"自动重建索引失败: {e}", exc_info=True)

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

            # 创建WebUI服务器实例（传递 ConversationManager 和 IndexValidator）
            self.webui_server = WebUIServer(
                memory_engine=self.memory_engine,
                config=webui_config,
                conversation_manager=self.conversation_manager,
                index_validator=self.index_validator,
            )

            # 启动WebUI服务器
            await self.webui_server.start()

            logger.info(
                f" WebUI 已启动: http://{webui_config.get('host', '127.0.0.1')}:{webui_config.get('port', 8080)}"
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

    async def _ensure_initialized(self, timeout: float = 30.0) -> bool:
        """确保插件已初始化（懒加载机制）"""
        # 如果已经初始化完成，直接返回
        if self._initialization_complete:
            return True

        # 如果初始化已失败，返回失败信息
        if self._initialization_failed:
            logger.warning(f"插件初始化已失败: {self._initialization_error}")
            return False

        # 等待初始化完成
        return await self._wait_for_initialization(timeout)

    async def _wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """等待插件初始化完成（内部方法）"""
        if self._initialization_complete:
            return True

        if self._initialization_failed:
            return False

        start_time = time.time()
        while not self._initialization_complete and not self._initialization_failed:
            if time.time() - start_time > timeout:
                logger.error(
                    f"等待插件初始化超时（{timeout}秒），当前状态：Provider尝试次数={self._provider_check_attempts}"
                )
                return False
            await asyncio.sleep(0.2)

        return self._initialization_complete

    def _get_webui_url(self) -> str | None:
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

    def _initialize_providers(self, silent: bool = False):
        """初始化 Embedding 和 LLM provider

        Args:
            silent: 静默模式，减少日志输出（用于轮询场景）
        """
        # 初始化 Embedding Provider
        emb_id = self.config.get("provider_settings", {}).get("embedding_provider_id")
        if emb_id:
            provider = self.context.get_provider_by_id(emb_id)
            # 类型检查：确保返回的是 EmbeddingProvider
            if provider and isinstance(provider, EmbeddingProvider):
                self.embedding_provider = provider
                if not silent:
                    logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")
            elif provider and not silent:
                logger.warning(f"Provider {emb_id} 不是 EmbeddingProvider 类型")

        if not self.embedding_provider:
            # 使用 AstrBot 标准 API 获取所有 Embedding Providers
            embedding_providers = self.context.get_all_embedding_providers()
            if embedding_providers:
                self.embedding_provider = embedding_providers[0]
                if not silent:
                    provider_id = getattr(
                        self.embedding_provider.provider_config,
                        "id",
                        self.embedding_provider.provider_config.get("id", "unknown"),
                    )
                    logger.info(f"未指定 Embedding Provider，使用默认的: {provider_id}")
            else:
                self.embedding_provider = None
                if not silent:
                    logger.debug("没有可用的 Embedding Provider")

        # 初始化 LLM Provider
        llm_id = self.config.get("provider_settings", {}).get("llm_provider_id")
        if llm_id:
            provider = self.context.get_provider_by_id(llm_id)
            # 类型检查：确保返回的是 Provider（LLM Provider）
            if provider and isinstance(provider, Provider):
                self.llm_provider = provider
                if not silent:
                    logger.info(f"成功从配置加载 LLM Provider: {llm_id}")
            elif provider and not silent:
                logger.warning(f"Provider {llm_id} 不是 LLM Provider 类型")

        if not self.llm_provider:
            self.llm_provider = self.context.get_using_provider()
            if not silent and self.llm_provider:
                logger.info("使用 AstrBot 当前默认的 LLM Provider。")

    def _remove_injected_memories_from_context(
        self, req: ProviderRequest, session_id: str
    ) -> int:
        """
        从对话历史和system_prompt中删除之前注入的记忆片段

        Args:
            req: Provider请求对象
            session_id: 会话ID

        Returns:
            int: 删除的消息数量
        """
        import re

        from .core.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

        removed_count = 0

        try:
            # 1. 清理 system_prompt 中的记忆
            if hasattr(req, "system_prompt") and req.system_prompt:
                if isinstance(req.system_prompt, str):
                    original_prompt = req.system_prompt
                    # 查找并删除记忆标记之间的内容
                    if (
                        MEMORY_INJECTION_HEADER in original_prompt
                        and MEMORY_INJECTION_FOOTER in original_prompt
                    ):
                        # 使用正则表达式删除所有记忆片段
                        pattern = (
                            re.escape(MEMORY_INJECTION_HEADER)
                            + r".*?"
                            + re.escape(MEMORY_INJECTION_FOOTER)
                        )
                        cleaned_prompt = re.sub(
                            pattern, "", original_prompt, flags=re.DOTALL
                        )
                        # 清理多余的空行
                        cleaned_prompt = re.sub(
                            r"\n{3,}", "\n\n", cleaned_prompt
                        ).strip()
                        req.system_prompt = cleaned_prompt

                        if cleaned_prompt != original_prompt:
                            removed_count += 1
                            logger.debug(
                                f"[{session_id}] 从 system_prompt 中删除记忆片段 "
                                f"(原始长度: {len(original_prompt)}, 清理后: {len(cleaned_prompt)})"
                            )

            # 2. 清理对话历史中的记忆
            # ProviderRequest 使用 contexts 属性（OpenAI 格式上下文列表）
            if hasattr(req, "contexts") and req.contexts:
                original_length = len(req.contexts)
                filtered_contexts = []

                for msg in req.contexts:
                    # 检查消息内容是否包含记忆标记
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                    if isinstance(content, str):
                        # 如果消息包含记忆注入标记，跳过该消息
                        if (
                            MEMORY_INJECTION_HEADER in content
                            and MEMORY_INJECTION_FOOTER in content
                        ):
                            removed_count += 1
                            logger.debug(
                                f"[{session_id}] 删除对话历史中的记忆片段: {content[:100]}..."
                            )
                            continue

                    filtered_contexts.append(msg)

                # 更新对话历史
                req.contexts = filtered_contexts

                if len(filtered_contexts) < original_length:
                    logger.debug(
                        f"[{session_id}] 从对话历史中删除了 {original_length - len(filtered_contexts)} 条记忆消息 "
                        f"(原始: {original_length}, 当前: {len(filtered_contexts)})"
                    )

            if removed_count > 0:
                logger.info(
                    f"[{session_id}] 成功清理旧记忆片段，共删除 {removed_count} 处注入内容"
                )

        except Exception as e:
            logger.error(f"[{session_id}] 删除注入记忆时发生错误: {e}", exc_info=True)

        return removed_count

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """[事件钩子] 在 LLM 请求前，查询并注入长期记忆"""
        if not await self._ensure_initialized():
            logger.debug("插件未完成初始化，跳过记忆召回")
            return

        if not self.memory_engine:
            logger.debug("记忆引擎尚未初始化，跳过记忆召回。")
            return

        try:
            # 使用 unified_msg_origin 作为 session_id，确保多Bot场景下的唯一性
            session_id = event.unified_msg_origin
            logger.debug(f"[DEBUG-Recall] 获取到 unified_msg_origin: {session_id}")

            async with OperationContext("记忆召回", session_id):
                # 首先检查是否需要自动删除旧的注入记忆
                auto_remove = self.config.get("recall_engine", {}).get(
                    "auto_remove_injected", True
                )
                if auto_remove:
                    self._remove_injected_memories_from_context(req, session_id)
                # 根据配置决定是否进行过滤
                filtering_config = self.config.get("filtering_settings", {})
                use_persona_filtering = filtering_config.get(
                    "use_persona_filtering", True
                )
                use_session_filtering = filtering_config.get(
                    "use_session_filtering", True
                )

                persona_id = await get_persona_id(self.context, event)

                # 调试：输出过滤参数
                logger.debug(
                    f"[{session_id}] 过滤参数: session_id={session_id}, persona_id={persona_id}, "
                    f"use_session={use_session_filtering}, use_persona={use_persona_filtering}"
                )

                recall_session_id = session_id if use_session_filtering else None
                recall_persona_id = persona_id if use_persona_filtering else None

                # 使用 MemoryEngine 进行智能回忆
                logger.info(
                    f"[{session_id}] 开始记忆召回，查询='{req.prompt[:50]}...'，top_k={self.config.get('recall_engine', {}).get('top_k', 5)}"
                )

                recalled_memories = await self.memory_engine.search_memories(
                    query=req.prompt,
                    k=self.config.get("recall_engine", {}).get("top_k", 5),
                    session_id=recall_session_id,
                    persona_id=recall_persona_id,
                )

                if recalled_memories:
                    logger.info(
                        f"[{session_id}] 检索到 {len(recalled_memories)} 条记忆"
                    )

                    # 格式化并注入记忆（包含完整元数据）
                    memory_list = [
                        {
                            "content": mem.content,
                            "score": mem.final_score,
                            "metadata": mem.metadata,  # 传递完整的元数据
                        }
                        for mem in recalled_memories
                    ]

                    # 输出详细的记忆信息
                    for i, mem in enumerate(recalled_memories, 1):
                        logger.debug(
                            f"[{session_id}] 记忆 #{i}: 得分={mem.final_score:.3f}, "
                            f"重要性={mem.metadata.get('importance', 0.5):.2f}, "
                            f"内容={mem.content[:100]}..."
                        )

                    # 根据配置选择记忆注入方式
                    injection_method = self.config.get("recall_engine", {}).get(
                        "injection_method", "system_prompt"
                    )

                    memory_str = format_memories_for_injection(memory_list)
                    logger.info(
                        f"[{session_id}] 格式化后的记忆字符串长度={len(memory_str)}, 注入方式={injection_method}"
                    )
                    logger.debug(
                        f"[{session_id}] 注入的记忆内容（前500字符）:\n{memory_str[:500]}"
                    )

                    if injection_method == "user_message_before":
                        # 在用户消息前插入记忆
                        req.prompt = memory_str + "\n\n" + req.prompt
                        logger.info(
                            f"[{session_id}]  成功向用户消息前注入 {len(recalled_memories)} 条记忆"
                        )
                    elif injection_method == "user_message_after":
                        # 在用户消息后插入记忆
                        req.prompt = req.prompt + "\n\n" + memory_str
                        logger.info(
                            f"[{session_id}]  成功向用户消息后注入 {len(recalled_memories)} 条记忆"
                        )
                    else:
                        # 默认：注入到 system_prompt
                        req.system_prompt = memory_str + "\n" + req.system_prompt
                        logger.info(
                            f"[{session_id}]  成功向 System Prompt 注入 {len(recalled_memories)} 条记忆"
                        )
                else:
                    logger.info(f"[{session_id}] 未找到相关记忆")

                # 使用 ConversationManager 添加用户消息
                if self.conversation_manager:
                    await self.conversation_manager.add_message_from_event(
                        event=event,
                        role="user",
                        content=req.prompt,
                    )

        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)

    @filter.on_llm_response()
    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """[事件钩子] 在 LLM 响应后，检查是否需要进行反思和记忆存储"""
        logger.debug(
            f"[DEBUG-Reflection] 进入 handle_memory_reflection，resp.role={resp.role}"
        )

        if not await self._ensure_initialized():
            logger.debug("插件未完成初始化，跳过记忆反思")
            return

        if (
            not self.memory_engine
            or not self.conversation_manager
            or resp.role != "assistant"
        ):
            logger.debug(
                f"[DEBUG-Reflection] 跳过反思 - memory_engine={self.memory_engine is not None}, "
                f"conversation_manager={self.conversation_manager is not None}, "
                f"resp.role={resp.role}"
            )
            return

        try:
            # 使用 unified_msg_origin 作为 session_id，确保多Bot场景下的唯一性
            session_id = event.unified_msg_origin
            logger.debug(f"[DEBUG-Reflection] 获取到 unified_msg_origin: {session_id}")
            if not session_id:
                logger.warning("[DEBUG-Reflection] session_id 为空，跳过反思")
                return

            # 使用 ConversationManager 添加助手响应
            await self.conversation_manager.add_message_from_event(
                event=event,
                role="assistant",
                content=resp.completion_text,
            )
            logger.debug(f"[DEBUG-Reflection] [{session_id}] 已添加助手响应消息")

            # 获取会话信息
            session_info = await self.conversation_manager.get_session_info(session_id)
            logger.debug(
                f"[DEBUG-Reflection] [{session_id}] session_info: {session_info}"
            )
            if not session_info:
                logger.warning(
                    f"[DEBUG-Reflection] [{session_id}] session_info 为 None，跳过反思"
                )
                return

            # 检查是否满足总结条件
            trigger_rounds = self.config.get("reflection_engine", {}).get(
                "summary_trigger_rounds", 10
            )
            logger.info(
                f"[DEBUG-Reflection] [{session_id}] 配置的 summary_trigger_rounds: {trigger_rounds}"
            )

            # 修复：基于对话轮数而非消息条数触发总结
            # 每轮对话 = 1条user消息 + 1条assistant消息 = 2条消息
            # 例如：trigger_rounds=5 表示每5轮对话触发，即每10条消息触发
            message_count = session_info.message_count
            conversation_rounds = message_count // 2  # 计算对话轮数

            logger.info(
                f"[DEBUG-Reflection] [{session_id}] 当前消息数: {message_count}, "
                f"对话轮数: {conversation_rounds}, 触发阈值(轮数): {trigger_rounds}"
            )
            logger.info(
                f"[DEBUG-Reflection] [{session_id}] 触发条件检查: "
                f"conversation_rounds >= trigger_rounds = {conversation_rounds >= trigger_rounds}, "
                f"conversation_rounds % trigger_rounds == 0 = {conversation_rounds % trigger_rounds == 0}"
            )

            # 每达到 trigger_rounds 轮对话的倍数时进行反思
            if (
                conversation_rounds >= trigger_rounds
                and conversation_rounds % trigger_rounds == 0
            ):
                logger.info(
                    f"[{session_id}]  对话轮数达到 {conversation_rounds} 轮（消息数={message_count}），启动记忆反思任务"
                )

                # ====== 滑动窗口逻辑 ======
                # 不再保留上下文，而是总结所有应该总结的消息

                # 获取上次总结的位置
                last_summarized_index = (
                    await self.conversation_manager.get_session_metadata(
                        session_id, "last_summarized_index", 0
                    )
                )

                # 计算本次需要总结的消息范围
                total_messages = session_info.message_count

                # end_index：总结到当前所有消息
                end_index = total_messages

                # start_index 计算：
                # 1. 如果是第一次总结（last_summarized_index == 0），从头开始
                # 2. 如果不是第一次，保留1-2轮重叠作为上下文
                # 3. 如果trigger_rounds < 2，则重叠轮数不超过trigger_rounds
                CONTEXT_OVERLAP_ROUNDS = 2  # 默认保留2轮对话作为上下文

                # 边界处理：重叠轮数不能超过trigger_rounds，且至少为1轮
                actual_overlap_rounds = min(
                    CONTEXT_OVERLAP_ROUNDS, max(1, trigger_rounds)
                )

                if last_summarized_index == 0:
                    # 第一次总结：从头开始
                    start_index = 0
                    context_rounds_added = 0
                else:
                    # 向前回溯N轮对话作为上下文
                    overlap_messages = actual_overlap_rounds * 2
                    start_index = max(0, last_summarized_index - overlap_messages)
                    context_rounds_added = min(
                        actual_overlap_rounds, last_summarized_index // 2
                    )

                logger.debug(
                    f"[{session_id}] 上下文重叠配置: trigger_rounds={trigger_rounds}, "
                    f"actual_overlap_rounds={actual_overlap_rounds}, context_rounds_added={context_rounds_added}"
                )

                # 严格限制：每次只总结 trigger_rounds 轮新对话
                # 计算实际应该总结到哪里（从last_summarized_index开始的trigger_rounds轮）
                max_new_messages = (
                    trigger_rounds * 2
                )  # trigger_rounds轮 = trigger_rounds*2条消息
                actual_end_index = last_summarized_index + max_new_messages

                # 不能超过当前总消息数
                if actual_end_index > total_messages:
                    actual_end_index = total_messages

                # 加上重叠上下文，确定最终范围
                end_index = actual_end_index

                # 计算本次将要总结的轮数
                messages_to_summarize = end_index - start_index
                rounds_to_summarize = messages_to_summarize // 2

                # 计算实际的新消息数（不含重叠部分）
                new_messages = end_index - last_summarized_index
                new_rounds = new_messages // 2

                logger.info(
                    f" [{session_id}] 滑动窗口总结: "
                    f"消息范围 [{start_index}:{end_index}]/{total_messages}, "
                    f"本次总结 {rounds_to_summarize} 轮（{messages_to_summarize} 条消息），"
                    f"其中包含 {context_rounds_added} 轮上下文 + {new_rounds} 轮新内容，"
                    f"上次总结位置 {last_summarized_index}"
                )

                # 检查是否有足够的新消息需要总结
                if end_index <= start_index:
                    logger.debug(
                        f"[{session_id}] 没有足够的新消息需要总结 "
                        f"(start={start_index}, end={end_index})"
                    )
                    return

                if new_rounds < 1:
                    logger.debug(f"[{session_id}] 没有新消息需要总结")
                    return

                # 获取需要总结的消息
                history_messages = await self.conversation_manager.get_messages_range(
                    session_id=session_id, start_index=start_index, end_index=end_index
                )

                logger.info(
                    f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结 "
                    f"(索引 {start_index} 到 {end_index})"
                )
                logger.debug(
                    f"[{session_id}] 历史消息预览: "
                    f"{[f'{m.role}:{m.content[:30]}...' for m in history_messages[:3]]}"
                )

                persona_id = await get_persona_id(self.context, event)

                # 调试：输出persona_id
                logger.debug(f"[{session_id}] 存储记忆时的persona_id={persona_id}")

                # 创建后台任务进行存储
                async def storage_task():
                    async with OperationContext("记忆存储", session_id):
                        try:
                            # 判断是否为群聊
                            is_group_chat = bool(
                                history_messages[0].group_id
                                if history_messages
                                else False
                            )

                            logger.info(
                                f"[{session_id}] 开始处理记忆，类型={'群聊' if is_group_chat else '私聊'}"
                            )

                            # 使用 MemoryProcessor 处理对话历史,生成结构化记忆
                            if self.memory_processor:
                                try:
                                    logger.info(
                                        f"[{session_id}] 调用 MemoryProcessor 处理 {len(history_messages)} 条消息"
                                    )
                                    # 获取是否保存原始对话的配置
                                    save_original = self.config.get(
                                        "reflection_engine", {}
                                    ).get("save_original_conversation", False)

                                    (
                                        content,
                                        metadata,
                                        importance,
                                    ) = await self.memory_processor.process_conversation(
                                        messages=history_messages,
                                        is_group_chat=is_group_chat,
                                        save_original=save_original,
                                    )
                                    logger.info(
                                        f"[{session_id}]  已使用LLM生成结构化记忆, "
                                        f"主题={metadata.get('topics', [])}, "
                                        f"情感={metadata.get('sentiment', 'neutral')}, "
                                        f"重要性={importance:.2f}"
                                    )
                                    logger.debug(
                                        f"[{session_id}] 记忆内容（前200字符）: {content[:200]}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"[{session_id}]  LLM处理失败,使用降级方案: {e}",
                                        exc_info=True,
                                    )
                                    # 降级方案：逐轮存储，带长度检查和截断
                                    logger.info(
                                        f"[{session_id}] 降级策略：将{len(history_messages)}条消息按轮次分别存储"
                                    )

                                    stored_count = 0
                                    skipped_count = 0

                                    for i in range(0, len(history_messages) - 1, 2):
                                        if i + 1 < len(history_messages):
                                            (
                                                success,
                                                error,
                                            ) = await store_round_with_length_check(
                                                self.memory_engine,
                                                history_messages[i],
                                                history_messages[i + 1],
                                                session_id,
                                                persona_id,
                                                i // 2,
                                            )
                                            if success:
                                                stored_count += 1
                                            else:
                                                skipped_count += 1

                                    logger.info(
                                        f"[{session_id}] 降级存储完成：成功{stored_count}轮，跳过{skipped_count}轮"
                                    )

                                    # 更新已总结位置后直接返回
                                    if self.conversation_manager:
                                        await self.conversation_manager.update_session_metadata(
                                            session_id,
                                            "last_summarized_index",
                                            end_index,
                                        )
                                    return  # 已经完成存储，直接返回
                            else:
                                # 如果 MemoryProcessor 未初始化,使用降级策略：逐轮存储
                                logger.warning(
                                    f"[{session_id}] MemoryProcessor未初始化,使用降级策略：逐轮存储"
                                )

                                # 按轮次存储，带长度检查和截断
                                stored_count = 0
                                skipped_count = 0

                                for i in range(0, len(history_messages) - 1, 2):
                                    if i + 1 < len(history_messages):
                                        (
                                            success,
                                            error,
                                        ) = await store_round_with_length_check(
                                            self.memory_engine,
                                            history_messages[i],
                                            history_messages[i + 1],
                                            session_id,
                                            persona_id,
                                            i // 2,
                                        )
                                        if success:
                                            stored_count += 1
                                        else:
                                            skipped_count += 1

                                logger.info(
                                    f"[{session_id}] 降级存储完成：成功{stored_count}轮，跳过{skipped_count}轮"
                                )

                                # 更新已总结的位置
                                if self.conversation_manager:
                                    await self.conversation_manager.update_session_metadata(
                                        session_id, "last_summarized_index", end_index
                                    )
                                    logger.info(
                                        f"[{session_id}]  更新滑动窗口位置: last_summarized_index = {end_index}"
                                    )
                                return  # 已完成存储，直接返回

                            # 正常流程：添加到记忆引擎（只有LLM处理成功时才会到这里）
                            logger.info(
                                f"[{session_id}] 准备存储记忆: 重要性={importance:.2f}, "
                                f"内容长度={len(content)}, metadata={list(metadata.keys())}"
                            )

                            if self.memory_engine:
                                await self.memory_engine.add_memory(
                                    content=content,
                                    session_id=session_id,
                                    persona_id=persona_id,
                                    importance=importance,
                                    metadata=metadata,
                                )

                                logger.info(
                                    f"[{session_id}]  成功存储对话记忆（{len(history_messages)}条消息，重要性={importance:.2f}）"
                                )
                            else:
                                logger.error(
                                    f"[{session_id}] memory_engine 为 None，无法存储记忆"
                                )

                            # 更新已总结的位置
                            if self.conversation_manager:
                                await self.conversation_manager.update_session_metadata(
                                    session_id, "last_summarized_index", end_index
                                )
                                logger.info(
                                    f"[{session_id}]  更新滑动窗口位置: last_summarized_index = {end_index}"
                                )
                        except Exception as e:
                            logger.error(
                                f"[{session_id}] 存储记忆失败: {e}", exc_info=True
                            )

                asyncio.create_task(storage_task())

        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)

    # --- 命令处理 ---
    @filter.command_group("lmem")
    def lmem_group(self):
        """长期记忆管理命令组 /lmem"""
        pass

    #
    # !!! [新功能] 新增一个 lmem reset 指令 !!!
    #
    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("reset")
    async def lmem_reset(self, event: AstrMessageEvent):
        """[管理员] 重置当前会话的长期记忆上下文，开始新的记忆篇章"""
        if not await self._ensure_initialized() or not self.conversation_manager:
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        session_id = self._get_session_id(event)
        try:
            await self.conversation_manager.clear_session(session_id)
            message = "✅ 当前会话的长期记忆上下文已重置。\n\n下一次记忆总结将从现在开始，不会再包含之前的对话内容。"
            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 重置失败: {str(e)}")

    # !!! 新增指令到这里结束 !!!

    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """从event获取session_id的辅助方法"""
        # 使用 unified_msg_origin 作为 session_id，确保多Bot场景下的唯一性
        return event.unified_msg_origin or "default"

    def _get_initialization_status_message(self) -> str:
        """获取初始化状态的用户友好消息"""
        if self._initialization_complete:
            return "✅ 插件已就绪"
        elif self._initialization_failed:
            return f"❌ 插件初始化失败: {self._initialization_error}\n\n请检查：\n1. Embedding Provider 是否已配置\n2. LLM Provider 是否可用\n3. 查看日志获取详细错误信息"
        else:
            return f"⏳ 插件正在后台初始化中...\n已尝试: {self._provider_check_attempts} 次\n\n如果长时间未完成，请检查：\n1. Embedding Provider 配置\n2. 其他插件是否阻塞了初始化流程"

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("status")
    async def lmem_status(self, event: AstrMessageEvent):
        """[管理员] 显示记忆系统状态"""
        if not await self._ensure_initialized():
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        if not self.memory_engine:
            yield event.plain_result(" 记忆引擎未初始化")
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
            db_size = 0.0
            if os.path.exists(self.memory_engine.db_path):
                db_size = os.path.getsize(self.memory_engine.db_path) / (1024 * 1024)

            session_count = len(stats.get("sessions", {}))

            message = f""" LivingMemory 状态报告

 总记忆数: {stats["total_memories"]}
 会话数: {session_count}
⏰ 最后更新: {last_update}
 数据库: {db_size:.2f} MB

使用 /lmem search <关键词> 搜索记忆
使用 /lmem webui 访问管理界面"""

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(f" 获取状态失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("search")
    async def lmem_search(self, event: AstrMessageEvent, query: str, k: int = 5):
        """[管理员] 搜索记忆"""
        if not await self._ensure_initialized():
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        if not self.memory_engine:
            yield event.plain_result(" 记忆引擎未初始化")
            return

        try:
            session_id = self._get_session_id(event)
            results = await self.memory_engine.search_memories(
                query=query, k=k, session_id=session_id
            )

            if not results:
                yield event.plain_result(f" 未找到与 '{query}' 相关的记忆")
                return

            message = f" 找到 {len(results)} 条相关记忆:\n\n"
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
            yield event.plain_result(f" 搜索失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("forget")
    async def lmem_forget(self, event: AstrMessageEvent, doc_id: int):
        """[管理员] 删除指定记忆"""
        if not await self._ensure_initialized():
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        if not self.memory_engine:
            yield event.plain_result(" 记忆引擎未初始化")
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                yield event.plain_result(f" 已删除记忆 #{doc_id}")
            else:
                yield event.plain_result(f" 删除失败，记忆 #{doc_id} 不存在")
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield event.plain_result(f" 删除失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("webui")
    async def lmem_webui(self, event: AstrMessageEvent):
        """[管理员] 显示WebUI访问信息"""
        if not await self._ensure_initialized():
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        webui_url = self._get_webui_url()

        if not webui_url:
            message = """️ WebUI 功能暂未启用

 WebUI 正在适配新的 MemoryEngine 架构
 预计在下一个版本中恢复

 当前可用功能:
• /lmem status - 查看系统状态
• /lmem search - 搜索记忆
• /lmem forget - 删除记忆"""
        else:
            message = f""" LivingMemory WebUI

访问地址: {webui_url}

 WebUI功能:
•  记忆编辑与管理
•  可视化统计分析
• ️ 高级配置管理
•  系统调试工具
•  数据迁移管理

在WebUI中可以进行更复杂的操作!"""

        yield event.plain_result(message)

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("rebuild-index")
    async def lmem_rebuild_index(self, event: AstrMessageEvent):
        """[管理员] 手动重建索引"""
        if not await self._ensure_initialized():
            status_msg = self._get_initialization_status_message()
            yield event.plain_result(status_msg)
            return

        if not self.memory_engine or not self.index_validator:
            yield event.plain_result(" 记忆引擎或索引验证器未初始化")
            return

        try:
            yield event.plain_result(" 开始检查索引状态...")

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if status.is_consistent and not status.needs_rebuild:
                yield event.plain_result(f" 索引状态正常: {status.reason}")
                return

            # 显示当前状态
            status_msg = f""" 当前索引状态:
• Documents表: {status.documents_count} 条
• BM25索引: {status.bm25_count} 条
• 向量索引: {status.vector_count} 条
• 问题: {status.reason}

开始重建索引..."""
            yield event.plain_result(status_msg)

            # 执行重建
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                result_msg = f""" 索引重建完成！

 处理结果:
• 成功: {result["processed"]} 条
• 失败: {result["errors"]} 条
• 总计: {result["total"]} 条

现在可以正常使用召回功能了！"""
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result(
                    f" 重建失败: {result.get('message', '未知错误')}"
                )

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield event.plain_result(f" 重建索引失败: {str(e)}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("help")
    async def lmem_help(self, event: AstrMessageEvent):
        """[管理员] 显示帮助信息"""
        message = """ LivingMemory 使用指南

 核心指令:
/lmem status              查看系统状态
/lmem search <关键词> [数量]  搜索记忆(默认5条)
/lmem forget <ID>          删除指定记忆
/lmem rebuild-index       重建v1迁移数据索引
/lmem webui               打开WebUI管理界面
/lmem help                显示此帮助

 使用建议:
• 日常查询使用 search 指令
• 复杂管理使用 WebUI 界面
• 记忆会自动保存对话内容
• 使用 forget 删除敏感信息
• v1迁移后需执行 rebuild-index

 更多信息: https://github.com/lxfight/astrbot_plugin_livingmemory"""

        yield event.plain_result(message)

    async def terminate(self):
        """插件停止时的清理逻辑"""
        logger.info("LivingMemory 插件正在停止...")

        # 停止并清理 WebUI 服务器
        if self.webui_server:
            try:
                logger.info("正在停止 WebUI 服务器...")

                # 停止定期清理任务
                if (
                    hasattr(self.webui_server, "_cleanup_task")
                    and self.webui_server._cleanup_task
                ):
                    if not self.webui_server._cleanup_task.done():
                        self.webui_server._cleanup_task.cancel()
                        try:
                            await self.webui_server._cleanup_task
                        except asyncio.CancelledError:
                            pass

                # 停止 uvicorn 服务器
                if hasattr(self.webui_server, "_server") and self.webui_server._server:
                    self.webui_server._server.should_exit = True

                if (
                    hasattr(self.webui_server, "_server_task")
                    and self.webui_server._server_task
                ):
                    try:
                        await self.webui_server._server_task
                    except (asyncio.CancelledError, KeyboardInterrupt, Exception):
                        # 忽略任务取消和中断异常
                        pass

                # 清理引用
                if hasattr(self.webui_server, "_server"):
                    self.webui_server._server = None
                if hasattr(self.webui_server, "_server_task"):
                    self.webui_server._server_task = None
                if hasattr(self.webui_server, "_cleanup_task"):
                    self.webui_server._cleanup_task = None

                self.webui_server = None
                logger.info(" WebUI 服务器已停止")

            except Exception as e:
                logger.error(f"停止 WebUI 服务器时出错: {e}", exc_info=True)
                self.webui_server = None

        # 关闭 ConversationManager（会自动关闭 ConversationStore）
        if self.conversation_manager and self.conversation_manager.store:
            await self.conversation_manager.store.close()
            logger.info(" ConversationManager 已关闭")

        # 关闭 MemoryEngine
        if self.memory_engine:
            await self.memory_engine.close()
            logger.info(" MemoryEngine 已关闭")

        # 关闭 FaissVecDB
        if self.db:
            await self.db.close()
            logger.info(" FaissVecDB 已关闭")

        logger.info("LivingMemory 插件已成功停止。")
