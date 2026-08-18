"""
MemoryHandler 的 MemoryHandlerUpdateMixin 拆分模块
自动从 core/page_api_modules/memory_handler.py 拆分，保持行为不变
"""

from typing import Any
from astrbot.api import logger
from quart import request
import time


class MemoryHandlerUpdateMixin:
    """MemoryHandler 拆分模块：MemoryHandlerUpdateMixin"""
    async def update_memory(self, memory_engine) -> dict[str, Any]:
        """
        更新单个记忆的字段

        支持的字段:
            - content: 记忆内容（会创建新记忆并删除旧记忆）
            - topics: 主题列表（会重建记忆及派生数据）
            - key_facts: 关键事实列表（会重建记忆及派生数据）
            - structured: 联合更新上述字段及其他元数据
            - importance: 重要性（0-1 或 0-10）
            - status: 状态（active/archived/deleted）
            - type: 类型

        Payload:
            - memory_id: 记忆ID（必需）
            - field: 要更新的字段（必需）
            - value: 新值（必需）
            - reason: 更新原因（可选）

        Returns:
            包含更新结果的字典
        """
        from ..utils.number_utils import clamp_float

        payload = await request.get_json(silent=True) or {}
        try:
            memory_id = int(payload.get("memory_id"))
        except (TypeError, ValueError):
            return self.utils.error("memory_id 必须是整数")

        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        value_scale = str(payload.get("value_scale", "auto")).strip().lower()
        reason = str(payload.get("reason", "")).strip()

        if not field or value is None:
            return self.utils.error("需要指定 field 和 value")

        memory = await self._get_memory_record(memory_id, memory_engine)
        if not memory:
            return self.utils.error("记忆不存在")

        current_metadata = self.utils.normalize_metadata(memory.get("metadata"))

        # 内容、主题和关键事实共同决定向量、原子与图数据，必须一次重建。
        if field in {"content", "topics", "key_facts", "structured"}:
            if field == "structured":
                if not isinstance(value, dict):
                    return self.utils.error("structured value 必须是对象")
                structured = value
            else:
                structured = {field: value}

            new_content = str(
                structured.get("content", memory.get("text", ""))
            ).strip()
            if not new_content:
                return self.utils.error("记忆内容不能为空")

            try:
                topics = self._normalize_edit_list(
                    structured.get("topics", current_metadata.get("topics", [])),
                    "topics",
                )
                key_facts = self._normalize_edit_list(
                    structured.get(
                        "key_facts", current_metadata.get("key_facts", [])
                    ),
                    "key_facts",
                )
                importance = (
                    self._normalize_importance_update(
                        structured["importance"], value_scale
                    )
                    if "importance" in structured
                    else clamp_float(
                        current_metadata.get("importance"), default=0.5
                    )
                )
            except ValueError as exc:
                return self.utils.error(str(exc))

            status_value = str(
                structured.get("status", current_metadata.get("status", "active"))
            ).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self.utils.error("状态必须是 active、archived 或 deleted")
            type_value = str(
                structured.get(
                    "type", current_metadata.get("memory_type", "GENERAL")
                )
            ).strip()
            if not type_value:
                return self.utils.error("类型不能为空")

            updated_at = time.time()
            replacement_metadata = current_metadata.copy()
            changes = {
                "content": (str(memory.get("text", "")), new_content),
                "topics": (current_metadata.get("topics", []), topics),
                "key_facts": (current_metadata.get("key_facts", []), key_facts),
                "status": (current_metadata.get("status", "active"), status_value),
                "type": (
                    current_metadata.get("memory_type", "GENERAL"),
                    type_value,
                ),
                "importance": (
                    clamp_float(current_metadata.get("importance"), default=0.5),
                    importance,
                ),
            }
            changed_fields = [
                name for name, (old, new) in changes.items() if old != new
            ]
            if not changed_fields:
                return self.utils.ok(
                    {
                        "message": "没有检测到修改",
                        "memory_id": memory_id,
                        "new_memory_id": memory_id,
                        "field": field,
                    }
                )

            for changed_field in changed_fields:
                old_value, new_value = changes[changed_field]
                replacement_metadata["update_history"] = (
                    self.utils.append_update_history(
                        replacement_metadata,
                        field=changed_field,
                        old_value=old_value,
                        new_value=new_value,
                        reason=reason,
                        timestamp=updated_at,
                    )
                )

            if reason:
                replacement_metadata["update_reason"] = reason
            replacement_metadata.update(
                {
                    "updated_at": updated_at,
                    "topics": topics,
                    "key_facts": key_facts,
                    "status": status_value,
                    "memory_type": type_value,
                    "importance": importance,
                }
            )
            if "content" in changed_fields:
                replacement_metadata["previous_content"] = str(
                    memory.get("text", "")
                )[:100]
                replacement_metadata["canonical_summary"] = new_content
                replacement_metadata["persona_summary"] = new_content

            try:
                new_memory_id = await memory_engine.replace_memory(
                    memory_id,
                    content=new_content,
                    importance=importance,
                    metadata=replacement_metadata,
                )
            except Exception as exc:
                logger.error(f"[PageAPI] 重建记忆失败: {exc}", exc_info=True)
                return self.utils.error(str(exc))

            return self.utils.ok(
                {
                    "message": f"记忆已更新（ID: {memory_id} → {new_memory_id}）",
                    "old_memory_id": memory_id,
                    "new_memory_id": new_memory_id,
                    "field": field,
                    "changed_fields": changed_fields,
                }
            )

        # 其他字段更新
        updates: dict[str, Any] = {}
        old_value_for_history: Any
        new_value_for_history: Any
        if field == "importance":
            try:
                normalized = self._normalize_importance_update(value, value_scale)
            except ValueError as exc:
                return self.utils.error(str(exc))
            updates["importance"] = normalized
            old_value_for_history = self.utils.importance_to_display(
                current_metadata.get("importance", 0.5)
            )
            new_value_for_history = round(normalized * 10.0, 2)
        elif field == "status":
            status_value = str(value).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self.utils.error("状态必须是 active、archived 或 deleted")
            current_status = str(current_metadata.get("status") or "active")
            if status_value == current_status:
                return self.utils.ok(
                    {
                        "message": "没有检测到修改",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            if status_value == "archived":
                archived = await memory_engine.archive_memories([memory_id])
                if archived != 1:
                    return self.utils.error("归档失败")
                return self.utils.ok(
                    {
                        "message": "记忆已归档并移出检索索引",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            if status_value == "active" and current_status == "archived":
                if not await memory_engine.restore_memory(memory_id):
                    return self.utils.error("恢复失败")
                return self.utils.ok(
                    {
                        "message": "记忆已恢复并重建检索索引",
                        "memory_id": memory_id,
                        "field": field,
                    }
                )
            updates["metadata"] = {"status": status_value}
            old_value_for_history = current_metadata.get("status", "active")
            new_value_for_history = status_value
        elif field == "type":
            type_value = str(value).strip()
            if not type_value:
                return self.utils.error("类型不能为空")
            updates["metadata"] = {"memory_type": type_value}
            old_value_for_history = current_metadata.get("memory_type", "GENERAL")
            new_value_for_history = type_value
        else:
            return self.utils.error(f"不支持编辑字段: {field}")

        updated_at = time.time()
        updates.setdefault("metadata", {})
        updates["metadata"]["update_history"] = self.utils.append_update_history(
            current_metadata,
            field=field,
            old_value=old_value_for_history,
            new_value=new_value_for_history,
            reason=reason,
            timestamp=updated_at,
        )
        updates["metadata"]["updated_at"] = updated_at

        if reason:
            updates["metadata"]["update_reason"] = reason

        try:
            success = await memory_engine.update_memory(memory_id, updates)
        except Exception as exc:
            logger.error(f"[PageAPI] 更新记忆失败: {exc}", exc_info=True)
            return self.utils.error(str(exc))

        if not success:
            return self.utils.error("更新失败")

        return self.utils.ok(
            {
                "message": f"记忆 {memory_id} 的 {field} 已更新",
                "memory_id": memory_id,
                "field": field,
            }
        )

    async def batch_delete_memories(self, memory_engine) -> dict[str, Any]:
        """
        批量删除记忆

        Payload:
            - memory_ids: 记忆ID列表（必需）

        Returns:
            包含删除统计的字典
        """
        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        if not isinstance(memory_ids, list) or not memory_ids:
            return self.utils.error("需要提供记忆 ID 列表")

        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []

        valid_ids: list[int] = []
        for raw_id in memory_ids:
            try:
                valid_ids.append(int(raw_id))
            except Exception:
                failed_count += 1
                failed_ids.append(raw_id)

        if valid_ids:
            deleted_count = await memory_engine.batch_delete_memories(valid_ids)

        return self.utils.ok(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_update_memories(self, memory_engine) -> dict[str, Any]:
        """
        批量更新记忆字段

        支持的字段:
            - status: 状态
            - importance: 重要性
            - type: 类型

        Payload:
            - memory_ids: 记忆ID列表（必需）
            - field: 要更新的字段（必需）
            - value: 新值（必需）

        Returns:
            包含更新统计的字典
        """
        payload = await request.get_json(silent=True) or {}
        memory_ids = payload.get("memory_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        value_scale = str(payload.get("value_scale", "auto")).strip().lower()

        if not isinstance(memory_ids, list) or not memory_ids:
            return self.utils.error("需要提供记忆 ID 列表")
        if not field or value is None:
            return self.utils.error("需要指定 field 和 value")

        if field not in ("status", "importance", "type"):
            return self.utils.error(f"批量更新不支持字段: {field}")

        updated_count = 0
        failed_ids: list[Any] = []

        for raw_id in memory_ids:
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue

            try:
                updates: dict[str, Any] = {}
                if field == "status":
                    status_value = str(value).strip()
                    if status_value not in {"active", "archived", "deleted"}:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"status": status_value}
                elif field == "importance":
                    try:
                        updates["importance"] = self._normalize_importance_update(
                            value, value_scale
                        )
                    except ValueError:
                        failed_ids.append(raw_id)
                        continue
                elif field == "type":
                    type_value = str(value).strip()
                    if not type_value:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"memory_type": type_value}

                success = await memory_engine.update_memory(memory_id, updates)
                if success:
                    updated_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception:
                failed_ids.append(raw_id)

        return self.utils.ok(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )
