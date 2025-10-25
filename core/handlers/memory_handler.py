# -*- coding: utf-8 -*-
"""
memory_handler.py - 记忆管理业务逻辑
处理记忆的编辑、更新、历史查看等业务逻辑
"""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from astrbot.api import logger
from astrbot.api.star import Context

from .base_handler import BaseHandler


class MemoryHandler(BaseHandler):
    """记忆管理业务逻辑处理器"""

    # 内容长度限制常量
    MAX_CONTENT_LENGTH = 10000  # 10KB

    def __init__(self, context: Context, config: Dict[str, Any], faiss_manager):
        super().__init__(context, config)
        self.faiss_manager = faiss_manager

    async def edit_memory(self, memory_id: str, field: str, value: str, reason: str = "") -> Dict[str, Any]:
        """编辑记忆内容或元数据
        
        注意: 此方法仅供 WebUI 使用，命令行指令 /lmem edit 已废弃
        """
        if not self.faiss_manager:
            return self.create_response(False, "记忆库尚未初始化")

        try:
            # 解析 memory_id 为整数或字符串
            try:
                memory_id_int = int(memory_id)
                memory_id_to_use = memory_id_int
            except ValueError:
                memory_id_to_use = memory_id

            # 解析字段和值
            updates = {}

            if field == "content":
                # 检查内容长度
                if len(value) > self.MAX_CONTENT_LENGTH:
                    return self.create_response(
                        False,
                        f"内容长度不能超过 {self.MAX_CONTENT_LENGTH} 字符 (当前: {len(value)})"
                    )
                updates["content"] = value
            elif field == "importance":
                try:
                    updates["importance"] = float(value)
                    if not 0.0 <= updates["importance"] <= 1.0:
                        return self.create_response(False, "重要性评分必须在 0.0 到 1.0 之间")
                except ValueError:
                    return self.create_response(False, "重要性评分必须是数字")
            elif field == "type":
                valid_types = ["FACT", "PREFERENCE", "GOAL", "OPINION", "RELATIONSHIP", "OTHER"]
                if value not in valid_types:
                    return self.create_response(False, f"无效的事件类型，必须是: {', '.join(valid_types)}")
                updates["event_type"] = value
            elif field == "status":
                valid_statuses = ["active", "archived", "deleted"]
                if value not in valid_statuses:
                    return self.create_response(False, f"无效的状态，必须是: {', '.join(valid_statuses)}")
                updates["status"] = value
            else:
                return self.create_response(False, f"未知的字段 '{field}'，支持的字段: content, importance, type, status")

            # 执行更新
            result = await self.faiss_manager.update_memory(
                memory_id=memory_id_to_use,
                update_reason=reason or f"更新{field}",
                **updates
            )

            if result["success"]:
                # 构建响应消息
                response_parts = [f"✅ {result['message']}"]
                
                if result["updated_fields"]:
                    response_parts.append("\n📋 已更新的字段:")
                    for f in result["updated_fields"]:
                        response_parts.append(f"  - {f}")
                
                # 如果更新了内容，显示预览
                if "content" in updates and len(updates["content"]) > 100:
                    response_parts.append(f"\n📝 内容预览: {updates['content'][:100]}...")
                
                return self.create_response(True, "\n".join(response_parts), result)
            else:
                return self.create_response(False, result['message'])

        except Exception as e:
            logger.error(f"编辑记忆时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"编辑记忆时发生错误: {e}")

    async def get_memory_details(self, memory_id: str) -> Dict[str, Any]:
        """获取记忆详细信息
        
        注意: 此方法仅供 WebUI 使用，命令行指令 /lmem info 已废弃
        """
        if not self.faiss_manager:
            return self.create_response(False, "记忆库尚未初始化")

        try:
            # 解析 memory_id
            try:
                memory_id_int = int(memory_id)
                docs = await self.faiss_manager.db.document_storage.get_documents(ids=[memory_id_int])
            except ValueError:
                docs = await self.faiss_manager.db.document_storage.get_documents(
                    metadata_filters={"memory_id": memory_id}
                )

            if not docs:
                return self.create_response(False, f"未找到ID为 {memory_id} 的记忆")

            doc = docs[0]
            metadata = self.safe_parse_metadata(doc["metadata"])

            # 构建详细信息
            details = {
                "id": memory_id,
                "content": doc["content"],
                "metadata": metadata,
                "create_time": self.format_timestamp(metadata.get("create_time")),
                "last_access_time": self.format_timestamp(metadata.get("last_access_time")),
                "importance": metadata.get("importance", "N/A"),
                "event_type": metadata.get("event_type", "N/A"),
                "status": metadata.get("status", "active"),
                "update_history": metadata.get("update_history", [])
            }

            return self.create_response(True, "获取记忆详细信息成功", details)

        except Exception as e:
            logger.error(f"获取记忆详细信息时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"获取记忆详细信息时发生错误: {e}")

    async def get_memory_history(self, memory_id: str) -> Dict[str, Any]:
        """获取记忆更新历史（已废弃，请使用 get_memory_info）"""
        return await self.get_memory_info(memory_id, show_edit_guide=False, full_history=True)

    async def get_memory_info(self, memory_id: str, show_edit_guide: bool = True,
                             full_history: bool = False) -> Dict[str, Any]:
        """获取记忆完整信息
        
        注意: 此方法仅供 WebUI 使用，命令行指令 /lmem info 已废弃

        参数:
            memory_id: 记忆 ID
            show_edit_guide: 是否显示编辑指引
            full_history: 是否显示完整更新历史
        """
        if not self.faiss_manager or not self.faiss_manager.db:
            return self.create_response(False, "记忆库尚未初始化")

        try:
            # 解析 memory_id
            try:
                memory_id_int = int(memory_id)
                docs = await self.faiss_manager.db.document_storage.get_documents(ids=[memory_id_int])
            except ValueError:
                docs = await self.faiss_manager.db.document_storage.get_documents(
                    metadata_filters={"memory_id": memory_id}
                )

            if not docs:
                return self.create_response(False, f"未找到ID为 {memory_id} 的记忆")

            doc = docs[0]
            metadata = self.safe_parse_metadata(doc["metadata"])

            # 构建信息
            info = {
                "id": memory_id,
                "content": doc["content"],
                "metadata": metadata,
                "create_time": self.format_timestamp(metadata.get("create_time")),
                "last_access_time": self.format_timestamp(metadata.get("last_access_time")),
                "importance": metadata.get("importance", "N/A"),
                "event_type": metadata.get("event_type", "N/A"),
                "status": metadata.get("status", "active"),
                "update_history": metadata.get("update_history", []),
                "show_edit_guide": show_edit_guide,
                "full_history": full_history
            }

            return self.create_response(True, "获取记忆信息成功", info)

        except Exception as e:
            logger.error(f"获取记忆信息时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"获取记忆信息时发生错误: {e}")

    def format_memory_details_for_display(self, details: Dict[str, Any]) -> str:
        """格式化记忆详细信息用于显示（已废弃，请使用 format_memory_info_for_display）"""
        return self.format_memory_info_for_display(details)

    def format_memory_history_for_display(self, history: Dict[str, Any]) -> str:
        """格式化记忆历史用于显示（已废弃，请使用 format_memory_info_for_display）"""
        return self.format_memory_info_for_display(history)

    def format_memory_info_for_display(self, info: Dict[str, Any]) -> str:
        """格式化记忆信息用于显示

        根据 info 中的 show_edit_guide 和 full_history 参数决定显示内容
        """
        if not info.get("success"):
            return info.get("message", "获取失败")

        data = info.get("data", {})
        show_edit_guide = data.get("show_edit_guide", True)
        full_history = data.get("full_history", False)

        response_parts = [f"📝 记忆 {data['id']} 的详细信息:"]
        response_parts.append("=" * 50)

        # 内容
        response_parts.append(f"\n📄 内容:")
        response_parts.append(f"{data['content']}")

        # 基本信息
        response_parts.append(f"\n📊 基本信息:")
        response_parts.append(f"- ID: {data['id']}")
        response_parts.append(f"- 重要性: {data['importance']}")
        response_parts.append(f"- 类型: {data['event_type']}")
        response_parts.append(f"- 状态: {data['status']}")

        # 时间信息
        if data['create_time'] != "未知":
            response_parts.append(f"- 创建时间: {data['create_time']}")
        if data.get('last_access_time') != "未知":
            response_parts.append(f"- 最后访问: {data['last_access_time']}")

        # 更新历史
        update_history = data.get('update_history', [])
        if update_history:
            # 根据 full_history 参数决定显示数量
            history_count = len(update_history) if full_history else min(3, len(update_history))
            response_parts.append(f"\n🔄 更新历史 ({len(update_history)} 次):")

            if full_history:
                displayed_history = update_history
            else:
                displayed_history = update_history[-3:]

            for i, update in enumerate(displayed_history, 1):
                timestamp = update.get('timestamp')
                if timestamp:
                    time_str = self.format_timestamp(timestamp)
                else:
                    time_str = "未知"

                response_parts.append(f"\n{i}. {time_str}")
                response_parts.append(f"   原因: {update.get('reason', 'N/A')}")
                response_parts.append(f"   字段: {', '.join(update.get('fields', []))}")

            if not full_history and len(update_history) > 3:
                response_parts.append(f"\n💡 使用 /lmem info {data['id']} --full 查看完整历史")
        else:
            response_parts.append("\n🔄 暂无更新记录")

        # 编辑指引（仅在 show_edit_guide=True 时显示）
        if show_edit_guide:
            response_parts.append(f"\n" + "=" * 50)
            response_parts.append(f"\n🛠️ 编辑指引:")
            response_parts.append(f"使用以下命令编辑此记忆:")
            response_parts.append(f"\n• 编辑内容:")
            response_parts.append(f"  /lmem edit {data['id']} content <新内容> [原因]")
            response_parts.append(f"\n• 编辑重要性:")
            response_parts.append(f"  /lmem edit {data['id']} importance <0.0-1.0> [原因]")
            response_parts.append(f"\n• 编辑类型:")
            response_parts.append(f"  /lmem edit {data['id']} type <FACT/PREFERENCE/GOAL/OPINION/RELATIONSHIP/OTHER> [原因]")
            response_parts.append(f"\n• 编辑状态:")
            response_parts.append(f"  /lmem edit {data['id']} status <active/archived/deleted> [原因]")

            # 示例
            response_parts.append(f"\n💡 示例:")
            response_parts.append(f"  /lmem edit {data['id']} importance 0.9 提高重要性评分")
            response_parts.append(f"  /lmem edit {data['id']} type PREFERENCE 重新分类为偏好")

        return "\n".join(response_parts)