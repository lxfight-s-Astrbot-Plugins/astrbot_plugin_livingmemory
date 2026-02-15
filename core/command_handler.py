"""
命令处理器
负责处理插件命令
"""

import os
import re
import socket
from collections.abc import AsyncGenerator
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER
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
            webui_server: WebUI服务器
            initialization_status_callback: 初始化状态回调函数
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self.webui_server = webui_server
        self.get_initialization_status = initialization_status_callback

    async def handle_status(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem status 命令"""
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

            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"获取状态失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 获取状态失败: {str(e)}")

    async def handle_search(
        self, event: AstrMessageEvent, query: str, k: int = 5
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem search 命令"""
        if not self.memory_engine:
            yield event.plain_result("❌ 记忆引擎未初始化")
            return

        # 输入验证
        if not query or not query.strip():
            yield event.plain_result("❌ 查询关键词不能为空")
            return

        # 限制k的范围为1-100
        k = max(1, min(k, 100))

        try:
            session_id = event.unified_msg_origin
            results = await self.memory_engine.search_memories(
                query=query.strip(), k=k, session_id=session_id
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

    async def handle_forget(
        self, event: AstrMessageEvent, doc_id: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem forget 命令"""
        if not self.memory_engine:
            yield event.plain_result("❌ 记忆引擎未初始化")
            return

        # 输入验证
        if doc_id < 0:
            yield event.plain_result("❌ 记忆ID必须为非负整数")
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

    async def handle_rebuild_index(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem rebuild-index 命令"""
        if not self.memory_engine or not self.index_validator:
            yield event.plain_result("❌ 记忆引擎或索引验证器未初始化")
            return

        try:
            yield event.plain_result("🔨 开始检查索引状态...")

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if status.is_consistent and not status.needs_rebuild:
                yield event.plain_result(f"✅ 索引状态正常: {status.reason}")
                return

            # 显示当前状态
            status_msg = f"""📊 当前索引状态:
• Documents表: {status.documents_count} 条
• BM25索引: {status.bm25_count} 条
• 向量索引: {status.vector_count} 条
• 问题: {status.reason}

🔨 开始重建索引..."""
            yield event.plain_result(status_msg)

            # 执行重建
            result = await self.index_validator.rebuild_indexes(self.memory_engine)

            if result["success"]:
                result_msg = f"""✅ 索引重建完成！

📊 处理结果:
• 成功: {result["processed"]} 条
• 失败: {result["errors"]} 条
• 总计: {result["total"]} 条

现在可以正常使用召回功能了！"""
                yield event.plain_result(result_msg)
            else:
                yield event.plain_result(
                    f"❌ 重建失败: {result.get('message', '未知错误')}"
                )

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 重建索引失败: {str(e)}")

    async def handle_webui(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem webui 命令"""
        webui_urls = self._get_webui_urls()

        if not webui_urls:
            message = """⚠️ WebUI 功能暂未启用

💡 WebUI 正在适配新的 MemoryEngine 架构
📅 预计在下一个版本中恢复

🔧 当前可用功能:
• /lmem status - 查看系统状态
• /lmem search - 搜索记忆
• /lmem forget - 删除记忆"""
        else:
            url_lines = "\n".join([f"• {url}" for url in webui_urls])
            message = f"""🌐 LivingMemory WebUI

🔗 访问地址:
{url_lines}

✨ WebUI功能:
• 📝 记忆编辑与管理
• 📊 可视化统计分析
• ⚙️ 高级配置管理
• 🔧 系统调试工具
• 🔄 数据迁移管理

在WebUI中可以进行更复杂的操作!"""

        yield event.plain_result(message)

    async def handle_reset(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem reset 命令"""
        if not self.conversation_manager:
            yield event.plain_result("❌ 会话管理器未初始化")
            return

        session_id = await self._resolve_conversation_session_id(event)
        try:
            await self.conversation_manager.clear_session(session_id)
            message = "✅ 当前会话的长期记忆上下文已重置。\n\n下一次记忆总结将从现在开始，不会再包含之前的对话内容。"
            yield event.plain_result(message)
        except Exception as e:
            logger.error(f"手动重置记忆上下文失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 重置失败: {str(e)}")

    async def handle_pending(
        self, event: AstrMessageEvent, n: int = 0
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem pending 命令 - 查看当前会话未总结消息"""
        if not self.conversation_manager:
            yield event.plain_result("❌ 会话管理器未初始化")
            return

        session_id = await self._resolve_conversation_session_id(event)
        trigger_rounds = self.config_manager.get(
            "reflection_engine.summary_trigger_rounds", 10
        )
        # 未显式传入数量时，默认使用记忆总结阈值-1（最小为1）
        if int(n) <= 0:
            n = max(1, int(trigger_rounds) - 1)
        # 预览条数限制，防止消息过长
        n = max(1, min(int(n), 100))

        try:
            total_messages = await self.conversation_manager.store.get_message_count(
                session_id
            )
            last_summarized_index = await self.conversation_manager.get_session_metadata(
                session_id, "last_summarized_index", 0
            )

            if last_summarized_index > total_messages:
                last_summarized_index = total_messages

            pending_messages = max(0, total_messages - last_summarized_index)

            if pending_messages == 0:
                yield event.plain_result(
                    "📭 当前会话没有待总结消息。\n\n可以继续对话，达到触发阈值后会自动总结。"
                )
                return

            pending_all = await self.conversation_manager.get_messages_range(
                session_id=session_id,
                start_index=last_summarized_index,
                end_index=total_messages,
            )
            pending_round_items = self._build_round_items(pending_all)
            pending_rounds = len(pending_round_items)
            remain_rounds = max(0, trigger_rounds - pending_rounds)

            preview_count = min(n, pending_rounds)
            preview_rounds = pending_round_items[-preview_count:]
            preview_start_no = pending_rounds - preview_count + 1

            lines = [
                "📌 当前会话待总结内容",
                "",
                f"• 待总结轮次: {pending_rounds} 轮",
                f"• 距离自动总结: 还差 {remain_rounds} 轮",
                "",
                f"🧾 最近待总结预览（{preview_count} 轮）:",
            ]

            for i, item in enumerate(preview_rounds, 1):
                round_no = preview_start_no + i - 1
                t = datetime.fromtimestamp(item["timestamp"]).strftime("%m-%d %H:%M")
                user_text = self._shorten_text(str(item.get("user", "")))
                assistant_text = self._shorten_text(str(item.get("assistant", "")))
                if user_text and assistant_text:
                    lines.append(
                        f"{round_no}. [{t}] 用户: {user_text} | 助手: {assistant_text}"
                    )
                elif user_text:
                    lines.append(f"{round_no}. [{t}] 用户: {user_text}")
                elif assistant_text:
                    lines.append(f"{round_no}. [{t}] 助手: {assistant_text}")

            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"查看待总结消息失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查看待总结消息失败: {str(e)}")

    async def handle_pending_del(
        self, event: AstrMessageEvent, round_no: int
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem pending-del 命令 - 删除待总结中的指定轮次"""
        if not self.conversation_manager:
            yield event.plain_result("❌ 会话管理器未初始化")
            return

        try:
            target_round = int(round_no)
        except Exception:
            yield event.plain_result("❌ 参数错误: 序号必须是整数")
            return

        if target_round <= 0:
            yield event.plain_result("❌ 参数错误: 序号必须大于 0")
            return

        session_id = await self._resolve_conversation_session_id(event)

        try:
            total_messages = await self.conversation_manager.store.get_message_count(
                session_id
            )
            last_summarized_index = await self.conversation_manager.get_session_metadata(
                session_id, "last_summarized_index", 0
            )
            if last_summarized_index > total_messages:
                last_summarized_index = total_messages

            pending_messages = max(0, total_messages - last_summarized_index)
            if pending_messages == 0:
                yield event.plain_result("📭 当前会话没有待总结消息，无需删除。")
                return

            pending_all = await self.conversation_manager.get_messages_range(
                session_id=session_id,
                start_index=last_summarized_index,
                end_index=total_messages,
            )
            pending_round_items = self._build_round_items(pending_all)
            pending_rounds = len(pending_round_items)

            if target_round > pending_rounds:
                yield event.plain_result(
                    f"❌ 序号越界: 当前待总结共 {pending_rounds} 轮，你输入的是 {target_round}。"
                )
                return

            target_item = pending_round_items[target_round - 1]
            message_ids = [
                int(mid)
                for mid in target_item.get("message_ids", [])
                if isinstance(mid, int) and int(mid) > 0
            ]
            if not message_ids:
                yield event.plain_result("❌ 目标轮次没有可删除的消息。")
                return

            if self.conversation_manager.store.connection is None:
                yield event.plain_result("❌ 数据库连接未初始化，删除失败。")
                return

            placeholders = ",".join("?" * len(message_ids))
            params = [session_id, *message_ids]
            cursor = await self.conversation_manager.store.connection.execute(
                f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                params,
            )
            deleted_count = cursor.rowcount if cursor.rowcount is not None else 0
            await self.conversation_manager.store.connection.commit()
            await self.conversation_manager.store.sync_message_counts()
            # 删除待总结消息后，旧的失败重试窗口索引可能失效，清空以避免错位重试
            await self.conversation_manager.update_session_metadata(
                session_id, "pending_summary", None
            )

            # 防御性修正：若删除后总消息减少，确保总结索引不越界
            new_total = await self.conversation_manager.store.get_message_count(session_id)
            if last_summarized_index > new_total:
                await self.conversation_manager.update_session_metadata(
                    session_id, "last_summarized_index", new_total
                )

            if deleted_count <= 0:
                yield event.plain_result("⚠️ 未删除任何消息，可能数据已变化，请先执行 /lmem pending 刷新。")
                return

            yield event.plain_result(
                f"✅ 已删除待总结第 {target_round} 轮，共 {deleted_count} 条消息。\n"
                "请重新执行 /lmem pending 查看最新序号。"
            )
        except Exception as e:
            logger.error(f"删除待总结轮次失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 删除失败: {str(e)}")

    async def handle_cleanup(
        self, event: AstrMessageEvent, dry_run: bool = False
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem cleanup 命令 - 清理 AstrBot 历史消息中的记忆注入片段"""
        session_id = event.unified_msg_origin
        try:
            mode_text = "[预演模式]" if dry_run else ""
            yield event.plain_result(
                f"🔄 {mode_text}开始清理 AstrBot 历史消息中的记忆注入片段..."
            )

            # 检查 context 是否可用
            if not self.context:
                yield event.plain_result("❌ 无法访问 AstrBot Context，清理失败")
                return

            # 获取当前对话 ID
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                session_id
            )
            if not cid:
                yield event.plain_result("❌ 当前会话没有对话历史，无需清理")
                return

            # 获取对话历史
            conversation = await self.context.conversation_manager.get_conversation(
                session_id, cid
            )
            if not conversation or not conversation.history:
                yield event.plain_result("❌ 当前对话历史为空，无需清理")
                return

            # 清理历史消息中的记忆注入片段
            import json
            import re

            from .base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

            # 解析 history（字符串格式）
            try:
                history = json.loads(conversation.history)
            except json.JSONDecodeError:
                yield event.plain_result("❌ 解析对话历史失败")
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
            message = f"""✅ {mode_text}清理完成!

📊 统计信息:
• 扫描消息: {stats["scanned"]} 条
• 匹配记忆片段: {stats["matched"]} 条
• 清理内容: {stats["cleaned"]} 条
• 删除消息: {stats["deleted"]} 条

{"💡 这是预演模式,未实际修改数据。使用 /lmem cleanup exec 执行实际清理。" if dry_run else "✨ AstrBot 对话历史已更新,记忆注入片段已清理。"}"""

            yield event.plain_result(message)

        except Exception as e:
            logger.error(f"清理历史消息失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 清理失败: {str(e)}")

    async def handle_help(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem help 命令"""
        message = """📖 LivingMemory 使用指南

🔧 核心指令:
/lmem status              查看系统状态
/lmem search <关键词> [数量]  搜索记忆(默认5条)
/lmem forget <ID>          删除指定记忆
/lmem rebuild-index       重建v1迁移数据索引
/lmem webui               打开WebUI管理界面
/lmem reset               重置当前会话记忆上下文
/lmem pending [数量]       查看当前会话待总结轮次预览(默认=总结阈值-1)
/lmem pending-del <序号>   删除待总结中的指定轮次
/lmem cleanup [preview|exec] 清理历史消息中的记忆片段(默认preview预演)
/lmem help                显示此帮助

💡 使用建议:
• 日常查询使用 search 指令
• 复杂管理使用 WebUI 界面
• 记忆会自动保存对话内容
• 使用 forget 删除敏感信息
• v1迁移后需执行 rebuild-index
• 更新插件后建议执行 cleanup 清理旧数据

📝 cleanup 命令示例:
  /lmem cleanup          # 预演模式,仅显示统计
  /lmem cleanup preview  # 同上
  /lmem cleanup exec     # 执行实际清理

📚 更多信息: https://github.com/lxfight/astrbot_plugin_livingmemory"""

        yield event.plain_result(message)

    def _get_webui_urls(self) -> list[str]:
        """获取 WebUI 可访问地址列表（优先可直连地址）"""
        webui_config = self.config_manager.webui_settings
        if not webui_config.get("enabled") or not self.webui_server:
            return []

        host = str(webui_config.get("host", "127.0.0.1")).strip()
        port = webui_config.get("port", 8080)
        urls: list[str] = []

        # 监听在所有网卡时，给出可用的本地地址和可选局域网地址
        if host in ["0.0.0.0", "::", ""]:
            local_ip = self._detect_local_ip()
            if local_ip:
                urls.append(f"http://{local_ip}:{port}")
            urls.append(f"http://127.0.0.1:{port}")
            return urls

        urls.append(f"http://{host}:{port}")
        return urls

    def _detect_local_ip(self) -> str | None:
        """探测当前主机局域网 IP（用于 WebUI 地址展示）"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and ip != "127.0.0.1":
                return ip
        except Exception:
            return None
        finally:
            if sock:
                sock.close()
        return None

    @staticmethod
    def _shorten_text(content: str, limit: int = 60) -> str:
        content = CommandHandler._strip_injected_memory(content)
        text = (content or "").replace("\n", " ").strip()
        if len(text) > limit:
            return text[:limit] + "..."
        return text

    @staticmethod
    def _strip_injected_memory(content: str) -> str:
        """仅用于展示时清理注入记忆片段，不修改原始存储内容。"""
        if not content:
            return ""
        if (
            MEMORY_INJECTION_HEADER not in content
            or MEMORY_INJECTION_FOOTER not in content
        ):
            return content
        pattern = (
            re.escape(MEMORY_INJECTION_HEADER)
            + r"\s*.*?\s*"
            + re.escape(MEMORY_INJECTION_FOOTER)
        )
        cleaned = re.sub(pattern, "", content, flags=re.DOTALL)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    def _build_round_items(messages: list) -> list[dict[str, str | float | list[int]]]:
        """按 user/assistant 组装轮次。"""
        rounds: list[dict[str, str | float | list[int]]] = []
        current: dict[str, str | float | list[int]] = {}

        for msg in messages:
            role = (getattr(msg, "role", "") or "").lower()
            content = getattr(msg, "content", "") or ""
            ts = float(getattr(msg, "timestamp", 0) or 0)
            message_id = int(getattr(msg, "id", 0) or 0)

            if role == "user":
                if current:
                    rounds.append(current)
                current = {"timestamp": ts, "user": content, "message_ids": [message_id]}
            elif role == "assistant":
                if not current:
                    current = {
                        "timestamp": ts,
                        "assistant": content,
                        "message_ids": [message_id],
                    }
                elif "assistant" in current:
                    rounds.append(current)
                    current = {
                        "timestamp": ts,
                        "assistant": content,
                        "message_ids": [message_id],
                    }
                else:
                    current["assistant"] = content
                    current_ids = current.get("message_ids", [])
                    if isinstance(current_ids, list):
                        current_ids.append(message_id)
            else:
                if current:
                    rounds.append(current)
                current = {"timestamp": ts, "user": content, "message_ids": [message_id]}

        if current:
            rounds.append(current)
        return rounds


    async def _resolve_conversation_session_id(self, event: AstrMessageEvent) -> str:
        """
        解析插件内部会话ID：unified_msg_origin + conversation_id。
        回退策略：无法获取conversation_id时返回unified_msg_origin。
        """
        base_session_id = event.unified_msg_origin
        if not self.context or not hasattr(self.context, "conversation_manager"):
            return base_session_id

        try:
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                base_session_id
            )
            if not cid:
                return base_session_id
            return f"{base_session_id}::conv::{cid}"
        except Exception as e:
            logger.debug(f"解析conversation_id失败，回退使用unified_msg_origin: {e}")
            return base_session_id
