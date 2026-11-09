"""
MemoryHandler 的 MemoryHandlerIoMixin 拆分模块
自动从 core/page_api_modules/memory_handler.py 拆分，保持行为不变
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from quart import request

from astrbot.api import logger

from ..memory_source import restore_source_messages
from ..memory_transfer import (
    memory_import_key,
    parse_transfer_content,
    plan_session_id_remap,
    serialize_transfer_csv,
    serialize_transfer_json,
)

# 全量导出上限：条数与序列化后的大致字节数（避免一次性把整库塞进响应）。
MAX_EXPORT_ENTRIES = 5000
MAX_EXPORT_APPROX_BYTES = 24 * 1024 * 1024
EXPORT_PAGE_SIZE = 500


def _record_approx_bytes(record: Any) -> int:
    """Estimate one transfer record's serialized size without double serializing."""
    return len(json.dumps(record, ensure_ascii=False, default=str))


def _fit_export_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Trim records until the serialized payload stays under the byte budget."""
    fitted: list[dict[str, Any]] = []
    approx = 0
    for record in records:
        size = _record_approx_bytes(record)
        if fitted and approx + size > MAX_EXPORT_APPROX_BYTES:
            return fitted, True
        fitted.append(record)
        approx += size
    return fitted, False


class MemoryHandlerIoMixin:
    """MemoryHandler 拆分模块：MemoryHandlerIoMixin"""

    async def _collect_export_memory_ids(
        self, memory_engine
    ) -> tuple[list[int], int, bool]:
        """Collect IDs for "export all" without loading every memory body first."""
        ids: list[int] = []
        page = 1
        total = 0
        while len(ids) < MAX_EXPORT_ENTRIES:
            page_size = min(EXPORT_PAGE_SIZE, MAX_EXPORT_ENTRIES - len(ids))
            page_data = await memory_engine.list_memories_page(
                page=page,
                page_size=page_size,
                status="all",
                sort_key="updated_desc",
            )
            total = int(page_data.get("total") or 0)
            items = page_data.get("items") or []
            if not items:
                break
            ids.extend(int(item["id"]) for item in items)
            if not page_data.get("has_more"):
                break
            page += 1
        ids = ids[:MAX_EXPORT_ENTRIES]
        return ids, total, total > len(ids)

    async def export_memories(self, memory_engine) -> dict[str, Any]:
        """Export all or selected memories as portable JSON/CSV content."""
        payload = await request.get_json(silent=True) or {}
        export_format = str(payload.get("format", "json")).strip().lower()
        if export_format not in {"json", "csv"}:
            return self.utils.error("导出格式仅支持 JSON 或 CSV")

        raw_ids = payload.get("memory_ids")
        truncated = False
        total_available = 0
        if raw_ids is None:
            (
                memory_ids,
                total_available,
                truncated,
            ) = await self._collect_export_memory_ids(memory_engine)
        else:
            if not isinstance(raw_ids, list):
                return self.utils.error("memory_ids 必须是整数列表")
            try:
                memory_ids = [int(item) for item in raw_ids]
            except (TypeError, ValueError):
                return self.utils.error("memory_ids 必须是整数列表")
            total_available = len(memory_ids)
            if len(memory_ids) > MAX_EXPORT_ENTRIES:
                memory_ids = memory_ids[:MAX_EXPORT_ENTRIES]
                truncated = True

        records = await memory_engine.get_memory_transfer_records(memory_ids)
        records, size_truncated = _fit_export_records(records)
        truncated = truncated or size_truncated
        exported_at = datetime.now(timezone.utc).isoformat()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 序列化可能涉及上万条记录（含完整 source_messages），卸载到线程避免阻塞事件循环
        if export_format == "csv":
            content = await asyncio.to_thread(serialize_transfer_csv, records)
            mime_type = "text/csv;charset=utf-8"
        else:
            content = await asyncio.to_thread(
                serialize_transfer_json, records, exported_at
            )
            mime_type = "application/json;charset=utf-8"

        return self.utils.ok(
            {
                "filename": f"livingmemory-export-{stamp}.{export_format}",
                "mime_type": mime_type,
                "content": content,
                "memory_count": len(records),
                "truncated": truncated,
                "total_available": total_available,
                "limit": MAX_EXPORT_ENTRIES,
            }
        )

    async def import_memories(
        self,
        memory_engine,
        memory_processor,
        *,
        current_platforms: Any = (),
        existing_session_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Preview or import native and common external JSON/CSV memories.

        Imported exports retain the platform segment of the deployment they were
        written in (``platform:message_type:session`` or a user memory scope).
        When that platform no longer exists, session filtering would never match
        the imported memories, so unambiguous platform segments are rewritten to
        the current deployment before duplicate detection and writing.
        """
        payload = await request.get_json(silent=True) or {}
        import_format = str(payload.get("format", "json")).strip().lower()
        content = payload.get("content")
        dry_run = bool(payload.get("dry_run", True))
        duplicate_strategy = (
            str(payload.get("duplicate_strategy", "skip")).strip().lower()
        )
        if duplicate_strategy not in {"skip", "allow"}:
            return self.utils.error("duplicate_strategy 仅支持 skip 或 allow")
        if not isinstance(content, str) or not content.strip():
            return self.utils.error("导入文件内容不能为空")
        if len(content.encode("utf-8")) > 50 * 1024 * 1024:
            return self.utils.error("导入文件不能超过 50 MiB")

        try:
            # 最多 50 MiB 内容的解析，卸载到线程避免阻塞事件循环
            entries, parse_errors = await asyncio.to_thread(
                parse_transfer_content, content, import_format
            )
        except ValueError as exc:
            return self.utils.error(str(exc))

        session_remap = plan_session_id_remap(
            [entry.session_id for entry in entries if entry.session_id],
            current_platforms,
            existing_session_ids or [],
        )
        remapped_count = 0
        for entry in entries:
            target = session_remap.get(entry.session_id) if entry.session_id else None
            if target:
                entry.session_id = target
                # 旧平台推导出的 adapter_id 同样失效，写入时按新会话前缀重新推导。
                entry.metadata.pop("adapter_id", None)
                remapped_count += 1

        existing_keys = await memory_engine.get_memory_import_keys()
        detected_keys = set(existing_keys)
        duplicate_count = 0
        for entry in entries:
            if not entry.content:
                continue
            key = memory_import_key(entry.content, entry.session_id, entry.persona_id)
            if key in detected_keys:
                duplicate_count += 1
            detected_keys.add(key)

        summary_required = sum(entry.requires_summary for entry in entries)
        if dry_run:
            planned_count = len(entries)
            if duplicate_strategy == "skip":
                planned_count -= duplicate_count
            return self.utils.ok(
                {
                    "dry_run": True,
                    "total": len(entries) + len(parse_errors),
                    "valid_count": len(entries),
                    "invalid_count": len(parse_errors),
                    "duplicate_count": duplicate_count,
                    "summary_required_count": summary_required,
                    "planned_import_count": max(0, planned_count),
                    "remapped_session_count": remapped_count,
                    "errors": parse_errors[:50],
                }
            )

        imported_ids: list[int] = []
        skipped_duplicates = 0
        import_errors = list(parse_errors)
        active_keys = set(existing_keys)
        for entry in entries:
            try:
                content_text = entry.content
                metadata = dict(entry.metadata)
                # add_memory 会用 metadata 覆盖入参 session_id/persona_id，
                # 这里显式写回导入解析后的目标值，避免旧平台前缀残留在检索过滤字段里。
                metadata["session_id"] = entry.session_id
                metadata["persona_id"] = entry.persona_id
                importance = entry.importance
                if entry.requires_summary:
                    if memory_processor is None:
                        raise RuntimeError(
                            "记忆处理器未初始化，无法总结仅含原始对话的导入项"
                        )
                    messages = restore_source_messages(entry.source_messages)
                    (
                        generated_content,
                        generated_metadata,
                        generated_importance,
                    ) = await memory_processor.process_conversation(
                        messages=messages,
                        is_group_chat=bool(messages[0].group_id),
                        persona_id=entry.persona_id,
                    )
                    content_text = generated_content
                    metadata.update(generated_metadata or {})
                    importance = generated_importance

                key = memory_import_key(
                    content_text, entry.session_id, entry.persona_id
                )
                if duplicate_strategy == "skip" and key in active_keys:
                    skipped_duplicates += 1
                    continue

                metadata["memory_origin"] = "memory_import"
                metadata["imported_at"] = time.time()
                metadata["import_source_index"] = entry.source_index
                importance = self._normalize_importance_update(importance, "stored")
                metadata["importance"] = importance
                atoms = None
                classify_atoms = getattr(
                    memory_processor, "classify_atoms_from_metadata", None
                )
                if callable(classify_atoms):
                    atoms = classify_atoms(
                        metadata=metadata,
                        parent_importance=importance,
                        session_id=entry.session_id,
                        persona_id=entry.persona_id,
                    )
                memory_id = await memory_engine.add_memory(
                    content=content_text,
                    session_id=entry.session_id,
                    persona_id=entry.persona_id,
                    importance=importance,
                    metadata=metadata,
                    atoms=atoms,
                    preserve_create_time="create_time" in metadata,
                    source_messages=entry.source_messages or None,
                )
                imported_ids.append(memory_id)
                active_keys.add(key)
            except Exception as exc:
                logger.error(
                    f"[PageAPI] 导入记忆失败 (index={entry.source_index}): {exc}",
                    exc_info=True,
                )
                import_errors.append({"index": entry.source_index, "error": str(exc)})

        return self.utils.ok(
            {
                "dry_run": False,
                "total": len(entries) + len(parse_errors),
                "imported_count": len(imported_ids),
                "skipped_duplicate_count": skipped_duplicates,
                "failed_count": len(import_errors),
                "imported_ids": imported_ids,
                "remapped_session_count": remapped_count,
                "errors": import_errors[:50],
            }
        )
