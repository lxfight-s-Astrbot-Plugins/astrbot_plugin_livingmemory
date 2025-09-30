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
    
    def __init__(self, context: Context, config: Dict[str, Any], faiss_manager):
        super().__init__(context, config)
        self.faiss_manager = faiss_manager
    
    async def process(self, *args, **kwargs) -> Dict[str, Any]:
        """处理请求的抽象方法实现"""
        return self.create_response(True, "MemoryHandler process method")
    
    async def edit_memory(self, memory_id: str, field: str, value: str, reason: str = "") -> Dict[str, Any]:
        """编辑记忆内容或元数据"""
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
        """获取记忆详细信息"""
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

            # 构建详细信息 (注意：doc中的key是text，但显示为content)
            details = {
                "id": memory_id,
                "content": doc.get("text", doc.get("content", "")),  # 兼容两种格式
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
        """获取记忆更新历史"""
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

            # 构建历史信息
            history_info = {
                "id": memory_id,
                "content": doc.get("text", doc.get("content", "")),  # 兼容两种格式
                "metadata": {
                    "importance": metadata.get("importance", "N/A"),
                    "event_type": metadata.get("event_type", "N/A"),
                    "status": metadata.get("status", "active"),
                    "create_time": self.format_timestamp(metadata.get("create_time"))
                },
                "update_history": metadata.get("update_history", [])
            }

            return self.create_response(True, "获取记忆历史成功", history_info)

        except Exception as e:
            logger.error(f"获取记忆历史时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"获取记忆历史时发生错误: {e}")

    def format_memory_details_for_display(self, details: Dict[str, Any]) -> str:
        """格式化记忆详细信息用于显示"""
        if not details.get("success"):
            return details.get("message", "获取失败")
        
        data = details.get("data", {})
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
        if data['last_access_time'] != "未知":
            response_parts.append(f"- 最后访问: {data['last_access_time']}")
        
        # 更新历史
        update_history = data.get('update_history', [])
        if update_history:
            response_parts.append(f"\n🔄 更新历史 ({len(update_history)} 次):")
            for i, update in enumerate(update_history[-3:], 1):  # 只显示最近3次
                timestamp = update.get('timestamp')
                if timestamp:
                    time_str = self.format_timestamp(timestamp)
                else:
                    time_str = "未知"
                
                response_parts.append(f"\n{i}. {time_str}")
                response_parts.append(f"   原因: {update.get('reason', 'N/A')}")
                response_parts.append(f"   字段: {', '.join(update.get('fields', []))}")
        
        # 编辑指引
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

    def format_memory_history_for_display(self, history: Dict[str, Any]) -> str:
        """格式化记忆历史用于显示"""
        if not history.get("success"):
            return history.get("message", "获取失败")
        
        data = history.get("data", {})
        metadata = data.get("metadata", {})
        
        response_parts = [f"📝 记忆 {data['id']} 的详细信息:"]
        response_parts.append(f"\n内容: {data['content']}")
        
        # 基本信息
        response_parts.append(f"\n📊 基本信息:")
        response_parts.append(f"- 重要性: {metadata['importance']}")
        response_parts.append(f"- 类型: {metadata['event_type']}")
        response_parts.append(f"- 状态: {metadata['status']}")
        
        # 时间信息
        if metadata.get('create_time') != "未知":
            response_parts.append(f"- 创建时间: {metadata['create_time']}")
        
        # 更新历史
        update_history = data.get('update_history', [])
        if update_history:
            response_parts.append(f"\n🔄 更新历史 ({len(update_history)} 次):")
            for i, update in enumerate(update_history[-5:], 1):  # 只显示最近5次
                timestamp = update.get('timestamp')
                if timestamp:
                    time_str = self.format_timestamp(timestamp)
                else:
                    time_str = "未知"
                
                response_parts.append(f"\n{i}. {time_str}")
                response_parts.append(f"   原因: {update.get('reason', 'N/A')}")
                response_parts.append(f"   字段: {', '.join(update.get('fields', []))}")
        else:
            response_parts.append("\n🔄 暂无更新记录")

        return "\n".join(response_parts)