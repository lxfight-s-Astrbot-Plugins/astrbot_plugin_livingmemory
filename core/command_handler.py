"""
命令处理器
负责处理插件命令
"""

import os
from collections.abc import AsyncGenerator
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .base.config_manager import ConfigManager
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .validators.index_validator import IndexValidator


class CommandHandler:
    """命令处理器"""

    def __init__(
        self,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine | None,
        conversation_manager: ConversationManager | None,
        index_validator: IndexValidator | None,
        webui_server=None,
        initialization_status_callback=None,
    ):
        """
        初始化命令处理器

        Args:
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            index_validator: 索引验证器
            webui_server: WebUI服务器
            initialization_status_callback: 初始化状态回调函数
        """
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self.webui_server = webui_server
        self.get_initialization_status = initialization_status_callback

    async def handle_status(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """处理 /lmem status 命令"""
        if not self.memory_engine:
            yield "❌ 记忆引擎未初始化"
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

            message = f"""📊 LivingMemory 状态报告

💾 总记忆数: {stats["total_memories"]}
👥 会话数: {session_count}
⏰ 最后更新: {last_update}
📁 数据库: {db_size:.2f} MB

使用 /lmem search <关键词> 搜索记忆
使用 /lmem webui 访问管理界面"""

            yield message
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield f"❌ 获取状态失败: {str(e)}"

    async def handle_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[str, None]:
        """处理 /lmem search 命令"""
        if not self.memory_engine:
            yield "❌ 记忆引擎未初始化"
            return

        try:
            session_id = event.unified_msg_origin
            results = await self.memory_engine.search_memories(
                query=query, k=k, session_id=session_id
            )

            if not results:
                yield f"🔍 未找到与 '{query}' 相关的记忆"
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

            yield message
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            yield f"❌ 搜索失败: {str(e)}"

    async def handle_forget(self, event: AstrMessageEvent, doc_id: int) -> AsyncGenerator[str, None]:
        """处理 /lmem forget 命令"""
        if not self.memory_engine:
            yield "❌ 记忆引擎未初始化"
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                yield f"✅ 已删除记忆 #{doc_id}"
            else:
                yield f"❌ 删除失败，记忆 #{doc_id} 不存在"
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield f"❌ 删除失败: {str(e)}"

    async def handle_rebuild_index(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """处理 /lmem rebuild-index 命令"""
        if not self.memory_engine or not self.index_validator:
            yield "❌ 记忆引擎或索引验证器未初始化"
            return

        try:
            yield "🔨 开始检查索引状态..."

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if status.is_consistent and not status.needs_rebuild:
                yield f"✅ 索引状态正常: {status.reason}"
                return

            # 显示当前状态
            status_msg = f"""📊 当前索引状态:
• Documents表: {status.documents_count} 条
• BM25索引: {status.bm25_count} 条
• 向量索引: {status.vector_count} 条
• 问题: {status.reason}

🔨 开始重建索引..."""
            yield status_msg

            # 执行重建
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                result_msg = f"""✅ 索引重建完成！

📊 处理结果:
• 成功: {result["processed"]} 条
• 失败: {result["errors"]} 条
• 总计: {result["total"]} 条

现在可以正常使用召回功能了！"""
                yield result_msg
            else:
                yield f"❌ 重建失败: {result.get('message', '未知错误')}"

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield f"❌ 重建索引失败: {str(e)}"

    async def handle_webui(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """处理 /lmem webui 命令"""
        webui_url = self._get_webui_url()

        if not webui_url:
            message = """⚠️ WebUI 功能暂未启用

💡 WebUI 正在适配新的 MemoryEngine 架构
📅 预计在下一个版本中恢复

🔧 当前可用功能:
• /lmem status - 查看系统状态
• /lmem search - 搜索记忆
• /lmem forget - 删除记忆"""
        else:
            message = f"""🌐 LivingMemory WebUI

🔗 访问地址: {webui_url}

✨ WebUI功能:
• 📝 记忆编辑与管理
• 📊 可视化统计分析
• ⚙️ 高级配置管理
• 🔧 系统调试工具
• 🔄 数据迁移管理

在WebUI中可以进行更复杂的操作!"""

        yield message

    async def handle_reset(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """处理 /lmem reset 命令"""
        if not self.conversation_manager:
            yield "❌ 会话管理器未初始化"
            return

        session_id = event.unified_msg_origin
        try:
            await self.conversation_manager.clear_session(session_id)
            message = "✅ 当前会话的长期记忆上下文已重置。\n\n下一次记忆总结将从现在开始，不会再包含之前的对话内容。"
            yield message
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield f"❌ 重置失败: {str(e)}"

    async def handle_help(self, event: AstrMessageEvent) -> AsyncGenerator[str, None]:
        """处理 /lmem help 命令"""
        message = """📖 LivingMemory 使用指南

🔧 核心指令:
/lmem status              查看系统状态
/lmem search <关键词> [数量]  搜索记忆(默认5条)
/lmem forget <ID>          删除指定记忆
/lmem rebuild-index       重建v1迁移数据索引
/lmem webui               打开WebUI管理界面
/lmem reset               重置当前会话记忆上下文
/lmem help                显示此帮助

💡 使用建议:
• 日常查询使用 search 指令
• 复杂管理使用 WebUI 界面
• 记忆会自动保存对话内容
• 使用 forget 删除敏感信息
• v1迁移后需执行 rebuild-index

📚 更多信息: https://github.com/lxfight/astrbot_plugin_livingmemory"""

        yield message

    def _get_webui_url(self) -> str | None:
        """获取 WebUI 访问地址"""
        webui_config = self.config_manager.webui_settings
        if not webui_config.get("enabled") or not self.webui_server:
            return None

        host = webui_config.get("host", "127.0.0.1")
        port = webui_config.get("port", 8080)

        if host in ["0.0.0.0", ""]:
            return f"http://127.0.0.1:{port}"
        else:
            return f"http://{host}:{port}"
