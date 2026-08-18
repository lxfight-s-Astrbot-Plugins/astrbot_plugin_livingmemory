"""
MemoryHandler 的 MemoryHandlerIoMixin 拆分模块
自动从 core/page_api_modules/memory_handler.py 拆分，保持行为不变
"""

from typing import Any
from datetime import datetime, timezone
from astrbot.api import logger
from ..memory_transfer import (    MAX_IMPORT_ENTRIES,    memory_import_key,    parse_transfer_content,    serialize_transfer_csv,    serialize_transfer_json,)
from quart import request
from ..memory_source import restore_source_messages
import time


class MemoryHandlerIoMixin:
    """MemoryHandler 拆分模块：MemoryHandlerIoMixin"""
    async def export_memories(self, memory_engine) -> dict[str, Any]:
        """Export all or selected memories as portable JSON/CSV content."""
        payload = await request.get_json(silent=True) or {}
        export_format = str(payload.get("format", "json")).strip().lower()
        if export_format not in {"json", "csv"}:
            return self.utils.error("导出格式仅支持 JSON 或 CSV")

        raw_ids = payload.get("memory_ids")
        memory_ids: list[int] | None = None
        if raw_ids is not None:
            if not isinstance(raw_ids, list):
                return self.utils.error("memory_ids 必须是整数列表")
            try:
                memory_ids = [int(item) for item in raw_ids]
            except (TypeError, ValueError):
                return self.utils.error("memory_ids 必须是整数列表")
            if len(memory_ids) > MAX_IMPORT_ENTRIES:
                return self.utils.error(
                    f"单次最多导出 {MAX_IMPORT_ENTRIES} 条记忆"
                )

        records = await memory_engine.get_memory_transfer_records(memory_ids)
        exported_at = datetime.now(timezone.utc).isoformat()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if export_format == "csv":
            content = serialize_transfer_csv(records)
            mime_type = "text/csv;charset=utf-8"
        else:
            content = serialize_transfer_json(records, exported_at)
            mime_type = "application/json;charset=utf-8"

        return self.utils.ok(
            {
                "filename": f"livingmemory-export-{stamp}.{export_format}",
                "mime_type": mime_type,
                "content": content,
                "memory_count": len(records),
            }
        )

    async def import_memories(
        self, memory_engine, memory_processor
    ) -> dict[str, Any]:
        """Preview or import native and common external JSON/CSV memories."""
        payload = await request.get_json(silent=True) or {}
        import_format = str(payload.get("format", "json")).strip().lower()
        content = payload.get("content")
        dry_run = bool(payload.get("dry_run", True))
        duplicate_strategy = str(
            payload.get("duplicate_strategy", "skip")
        ).strip().lower()
        if duplicate_strategy not in {"skip", "allow"}:
            return self.utils.error("duplicate_strategy 仅支持 skip 或 allow")
        if not isinstance(content, str) or not content.strip():
            return self.utils.error("导入文件内容不能为空")
        if len(content.encode("utf-8")) > 50 * 1024 * 1024:
            return self.utils.error("导入文件不能超过 50 MiB")

        try:
            entries, parse_errors = parse_transfer_content(content, import_format)
        except ValueError as exc:
            return self.utils.error(str(exc))

        existing_keys = await memory_engine.get_memory_import_keys()
        detected_keys = set(existing_keys)
        duplicate_count = 0
        for entry in entries:
            if not entry.content:
                continue
            key = memory_import_key(
                entry.content, entry.session_id, entry.persona_id
            )
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
                importance = entry.importance
                if entry.requires_summary:
                    if memory_processor is None:
                        raise RuntimeError(
                            "记忆处理器未初始化，无法总结仅含原始对话的导入项"
                        )
                    messages = restore_source_messages(entry.source_messages)
                    generated_content, generated_metadata, generated_importance = (
                        await memory_processor.process_conversation(
                            messages=messages,
                            is_group_chat=bool(messages[0].group_id),
                            persona_id=entry.persona_id,
                        )
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
                importance = self._normalize_importance_update(
                    importance, "stored"
                )
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
                import_errors.append(
                    {"index": entry.source_index, "error": str(exc)}
                )

        return self.utils.ok(
            {
                "dry_run": False,
                "total": len(entries) + len(parse_errors),
                "imported_count": len(imported_ids),
                "skipped_duplicate_count": skipped_duplicates,
                "failed_count": len(import_errors),
                "imported_ids": imported_ids,
                "errors": import_errors[:50],
            }
        )
