"""
命令处理器
负责处理插件命令
"""

import os
from collections.abc import AsyncGenerator
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .base.config_manager import ConfigManager
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .validators.index_validator import IndexValidator


class CommandHandler:
    """命令处理器"""

    def __init__(
        self,
        context,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine | None,
        conversation_manager: ConversationManager | None,
        index_validator: IndexValidator | None,
        memory_processor=None,
        webui_server=None,
        initialization_status_callback=None,
    ):
        """
        初始化命令处理器

        Args:
            context: AstrBot Context
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            index_validator: 索引验证器
            memory_processor: 记忆处理器（用于手动总结）
            webui_server: WebUI服务器
            initialization_status_callback: 初始化状态回调函数
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self._memory_processor = memory_processor
        self.webui_server = webui_server
        self.get_initialization_status = initialization_status_callback

    @staticmethod
    def _format_error_message(
        action: str, error: Exception, suggestions: list[str] | None = None
    ) -> str:
        """Format user-facing error message with actionable hints."""
        message = [f"{action}失败。", f"错误详情: {error}"]
        if suggestions:
            message.append("")
            message.append("建议排查:")
            for index, suggestion in enumerate(suggestions, start=1):
                message.append(f"{index}. {suggestion}")
        return "\n".join(message)

    @staticmethod
    def _component_not_ready_message(component: str, command: str) -> str:
        """Build a consistent component-not-ready response."""
        return (
            f"{command} 执行失败：{component}未初始化。\n"
            "请先执行 /lmem status 检查插件状态；如状态异常请查看启动日志。"
        )

    async def handle_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem status 命令"""
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/lmem status")
            )
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

            message = f"""LivingMemory 状态报告

总记忆数: {stats["total_memories"]}
会话数: {session_count}
最后更新: {last_update}
数据库大小: {db_size:.2f} MB

可用操作:
- /lmem search <关键词>
- /lmem webui"""

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "获取状态",
                    e,
                    [
                        "确认数据库文件可读写",
                        "确认记忆引擎已完成初始化",
                        "查看日志中的异常堆栈定位具体模块",
                    ],
                )
            )

    async def handle_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem search 命令"""
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/lmem search")
            )
            return

        # 输入验证
        if not query or not query.strip():
            yield event.plain_result(
                "查询关键词不能为空。示例: /lmem search 项目进度 5"
            )
            return

        # 限制k的范围为1-100
        k = max(1, min(k, 100))

        try:
            session_id = event.unified_msg_origin
            results = await self.memory_engine.search_memories(
                query=query.strip(), k=k, session_id=session_id
            )

            if not results:
                yield event.plain_result(
                    f"未找到与 '{query}' 相关的记忆。可尝试更短关键词，"
                    "或调大返回数量参数 k。"
                )
                return

            message = f"找到 {len(results)} 条相关记忆:\n\n"
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
            yield event.plain_result(
                self._format_error_message(
                    "搜索",
                    e,
                    [
                        "确认关键词不为空且长度合理",
                        "确认数据库和索引文件存在且可读写",
                        "检查日志中是否有检索组件初始化失败信息",
                    ],
                )
            )

    async def handle_forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem forget 命令"""
        if not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message("记忆引擎", "/lmem forget")
            )
            return

        # 输入验证
        if doc_id < 0:
            yield event.plain_result("记忆 ID 必须为非负整数。示例: /lmem forget 123")
            return

        try:
            success = await self.memory_engine.delete_memory(doc_id)
            if success:
                yield event.plain_result(f"已删除记忆 #{doc_id}。")
            else:
                yield event.plain_result(
                    f"删除失败：记忆 #{doc_id} 不存在。\n"
                    "请先使用 /lmem search 或 WebUI 确认记忆 ID。"
                )
        except Exception as e:
            logger.error(f"删除失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "删除记忆",
                    e,
                    [
                        "确认记忆 ID 存在且属于当前可访问数据",
                        "确认数据库未被其他进程长时间占用",
                        "查看日志中的删除调用堆栈",
                    ],
                )
            )

    async def handle_rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem rebuild-index 命令"""
        if not self.memory_engine or not self.index_validator:
            yield event.plain_result(
                self._component_not_ready_message(
                    "记忆引擎或索引验证器", "/lmem rebuild-index"
                )
            )
            return

        try:
            yield event.plain_result("开始检查索引状态...")

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if status.is_consistent and not status.needs_rebuild:
                yield event.plain_result(f"索引状态正常: {status.reason}")
                return

            # 显示当前状态
            status_msg = f"""当前索引状态:
• Documents表: {status.documents_count} 条
• BM25索引: {status.bm25_count} 条
• 向量索引: {status.vector_count} 条
• 问题: {status.reason}

开始重建索引..."""
            yield event.plain_result(status_msg)

            # 执行重建
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                result_msg = f"""索引重建完成。

处理结果:
• 成功: {result["processed"]} 条
• 失败: {result["errors"]} 条
• 总计: {result["total"]} 条

现在可以继续使用召回功能。"""
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result(
                    "索引重建失败。\n"
                    f"错误详情: {result.get('message', '未知错误')}\n"
                    "请查看日志确认失败原因后重试 /lmem rebuild-index。"
                )

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "重建索引",
                    e,
                    [
                        "确认 Embedding Provider 已可用",
                        "确认数据库文件与索引文件可读写",
                        "根据日志定位失败文档后重试重建",
                    ],
                )
            )

    async def handle_webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem webui 命令"""
        webui_url = self._get_webui_url()

        if not webui_url:
            message = """WebUI 功能当前未启用。

可能原因:
1. 配置中 webui.enabled=false
2. WebUI 服务启动失败（请查看日志）

当前可用功能:
• /lmem status - 查看系统状态
• /lmem search - 搜索记忆
• /lmem forget - 删除记忆"""
        else:
            message = f"""LivingMemory WebUI

访问地址: {webui_url}

WebUI 功能:
• 记忆编辑与管理
• 可视化统计分析
• 高级配置管理
• 系统调试工具
• 数据迁移管理

可在 WebUI 中执行更复杂的管理操作。"""

        yield event.plain_result(message)

    async def handle_summarize(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem summarize 命令 - 立即触发记忆总结"""
        if not self.conversation_manager or not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message(
                    "会话管理器或记忆引擎", "/lmem summarize"
                )
            )
            return

        session_id = event.unified_msg_origin
        try:
            # 获取当前消息数和总结进度
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )
            try:
                last_summarized_index = int(last_summarized_index)
            except (TypeError, ValueError):
                last_summarized_index = 0

            unsummarized = actual_count - last_summarized_index

            if unsummarized < 2:
                yield event.plain_result(
                    "当前没有需要总结的新对话。\n"
                    f"当前消息总数: {actual_count}\n"
                    f"已总结到消息序号: {last_summarized_index}"
                )
                return

            yield event.plain_result(
                f"开始手动总结记忆...\n"
                f"消息范围: [{last_summarized_index}:{actual_count}]，共 {unsummarized} 条"
            )

            history_messages = await self.conversation_manager.get_messages_range(
                session_id=session_id,
                start_index=last_summarized_index,
                end_index=actual_count,
            )

            if not history_messages:
                yield event.plain_result(
                    "获取消息失败：未读取到可总结的消息。\n"
                    "请确认当前会话存在历史消息后重试。"
                )
                return

            # 获取 persona_id
            from .utils import get_persona_id

            persona_id = await get_persona_id(self.context, event)

            # 判断是否群聊
            is_group_chat = bool(
                history_messages[0].group_id if history_messages else False
            )
            if not is_group_chat and "GroupMessage" in session_id:
                is_group_chat = True

            save_original = self.config_manager.get(
                "reflection_engine.save_original_conversation", False
            )

            if not self._memory_processor:
                yield event.plain_result(
                    self._component_not_ready_message("记忆处理器", "/lmem summarize")
                )
                return

            (
                content,
                metadata,
                importance,
            ) = await self._memory_processor.process_conversation(
                messages=history_messages,
                is_group_chat=is_group_chat,
                save_original=save_original,
                persona_id=persona_id,
            )

            metadata["source_window"] = {
                "session_id": session_id,
                "start_index": last_summarized_index,
                "end_index": actual_count,
                "message_count": actual_count - last_summarized_index,
                "triggered_by": "manual",
            }

            await self.memory_engine.add_memory(
                content=content,
                session_id=session_id,
                persona_id=persona_id,
                importance=importance,
                metadata=metadata,
            )

            await self.conversation_manager.update_session_metadata(
                session_id, "last_summarized_index", actual_count
            )
            await self.conversation_manager.update_session_metadata(
                session_id, "pending_summary", None
            )

            topics = ", ".join(metadata.get("topics", [])) or "无"
            yield event.plain_result(
                f"记忆总结完成。\n"
                f"重要性: {importance:.2f}\n"
                f"主题: {topics}\n"
                f"已更新总结进度至第 {actual_count} 条消息"
            )

        except Exception as e:
            logger.error(f"手动触发记忆总结失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "记忆总结",
                    e,
                    [
                        "确认当前会话至少有 2 条未总结消息",
                        "确认 LLM Provider 可正常响应",
                        "检查日志中的 summary 处理堆栈",
                    ],
                )
            )

    async def handle_reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem reset 命令"""
        if not self.conversation_manager:
            yield event.plain_result(
                self._component_not_ready_message("会话管理器", "/lmem reset")
            )
            return

        session_id = event.unified_msg_origin
        try:
            await self.conversation_manager.clear_session(session_id)
            message = (
                "当前会话的长期记忆上下文已重置。\n\n"
                "下一次记忆总结将从现在开始，不会再包含之前的对话内容。"
            )
            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "重置记忆上下文",
                    e,
                    [
                        "确认会话 ID 有效且会话存储可访问",
                        "确认数据库未被占用",
                        "查看日志中的 clear_session 调用堆栈",
                    ],
                )
            )

    async def handle_cleanup(
        self, event: AstrMessageEvent, dry_run: bool = False
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem cleanup 命令 - 清理 AstrBot 历史消息中的记忆注入片段"""
        session_id = event.unified_msg_origin
        try:
            mode_text = "预演模式：" if dry_run else ""
            yield event.plain_result(
                f"{mode_text}开始清理 AstrBot 历史消息中的记忆注入片段..."
            )

            # 检查 context 是否可用
            if not self.context:
                yield event.plain_result(
                    "清理失败：无法访问 AstrBot Context。\n"
                    "请确认插件运行在完整 AstrBot 上下文中后重试。"
                )
                return

            # 获取当前对话 ID
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                session_id
            )
            if not cid:
                yield event.plain_result("当前会话没有对话历史，无需清理。")
                return

            # 获取对话历史
            conversation = await self.context.conversation_manager.get_conversation(
                session_id, cid
            )
            if not conversation or not conversation.history:
                yield event.plain_result("当前对话历史为空，无需清理。")
                return

            # 清理历史消息中的记忆注入片段
            import json
            import re

            from .base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

            # 解析 history（字符串格式）
            try:
                history = json.loads(conversation.history)
            except json.JSONDecodeError:
                yield event.plain_result(
                    "解析对话历史失败：数据不是有效 JSON。\n"
                    "请检查会话存储内容是否被外部工具修改。"
                )
                return

            # 统计信息
            stats = {
                "scanned": len(history),
                "matched": 0,
                "cleaned": 0,
                "deleted": 0,
            }

            # 编译清理正则
            pattern = re.compile(
                re.escape(MEMORY_INJECTION_HEADER)
                + r".*?"
                + re.escape(MEMORY_INJECTION_FOOTER),
                flags=re.DOTALL,
            )

            # 清理历史消息
            cleaned_history = []
            for msg in history:
                content = msg.get("content", "")
                if not isinstance(content, str):
                    cleaned_history.append(msg)
                    continue

                # 检查是否包含注入标记
                if (
                    MEMORY_INJECTION_HEADER in content
                    and MEMORY_INJECTION_FOOTER in content
                ):
                    stats["matched"] += 1

                    # 清理内容
                    cleaned_content = pattern.sub("", content)
                    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

                    # 如果清理后为空，跳过该消息
                    if not cleaned_content:
                        stats["deleted"] += 1
                        logger.debug(
                            f"[cleanup] 删除纯记忆注入消息: role={msg.get('role')}"
                        )
                        continue

                    # 如果清理后仍有内容，保留清理后的消息
                    if cleaned_content != content:
                        msg_copy = msg.copy()
                        msg_copy["content"] = cleaned_content
                        cleaned_history.append(msg_copy)
                        stats["cleaned"] += 1
                        logger.debug(
                            f"[cleanup] 清理消息内部记忆片段: "
                            f"原长度={len(content)}, 新长度={len(cleaned_content)}"
                        )
                        continue

                cleaned_history.append(msg)

            # 如果不是预演模式，更新数据库
            if not dry_run and (stats["cleaned"] > 0 or stats["deleted"] > 0):
                await self.context.conversation_manager.update_conversation(
                    unified_msg_origin=session_id,
                    conversation_id=cid,
                    history=cleaned_history,
                )
                logger.info(
                    f"[{session_id}] cleanup 已更新 AstrBot 对话历史: "
                    f"清理={stats['cleaned']}, 删除={stats['deleted']}"
                )

            # 格式化结果
            message = f"""{mode_text}清理完成。

统计信息:
• 扫描消息: {stats["scanned"]} 条
• 匹配记忆片段: {stats["matched"]} 条
• 清理内容: {stats["cleaned"]} 条
• 删除消息: {stats["deleted"]} 条

{"这是预演模式，未实际修改数据。使用 /lmem cleanup exec 执行实际清理。" if dry_run else "AstrBot 对话历史已更新，记忆注入片段已清理。"}"""

            yield event.plain_result(message)

        except Exception as e:
            logger.error(f"清理历史消息失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    "清理历史消息",
                    e,
                    [
                        "确认当前会话存在可读取的历史记录",
                        "确认对话存储可读写",
                        "查看日志中的 cleanup 调用堆栈",
                    ],
                )
            )

    async def handle_help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem help 命令"""
        message = """LivingMemory 使用指南

核心指令:
/lmem status              查看系统状态
/lmem search <关键词> [数量]  搜索记忆(默认5条)
/lmem forget <ID>          删除指定记忆
/lmem rebuild-index       重建索引（修复索引不一致）
/lmem webui               打开WebUI管理界面
/lmem summarize           立即触发当前会话的记忆总结
/lmem reset               重置当前会话记忆上下文
/lmem cleanup [preview|exec] 清理历史消息中的记忆片段(默认preview预演)
/lmem help                显示此帮助

使用建议:
• 日常查询使用 search 指令
• 复杂管理使用 WebUI 界面
• 记忆会自动保存对话内容
• 使用 forget 删除敏感信息
• 索引不一致时执行 rebuild-index
• 更新插件后建议执行 cleanup 清理旧数据

cleanup 命令示例:
  /lmem cleanup          # 预演模式,仅显示统计
  /lmem cleanup preview  # 同上
  /lmem cleanup exec     # 执行实际清理

更多信息: https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory"""

        yield event.plain_result(message)

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
