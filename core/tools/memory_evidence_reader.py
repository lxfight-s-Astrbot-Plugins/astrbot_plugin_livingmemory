"""Beta 共用证据读取：每次校验权限与生命周期，分页留给最终输出层。"""

import asyncio
from typing import Any


async def read_memory_evidence_core(
    *,
    memory_engine: Any,
    recall_session_id: str | None,
    recall_persona_id: str | None,
    memory_id: int,
    include_source: bool = False,
) -> dict[str, Any]:
    """加载当前可见证据；不存在与越权不可区分，真实存储错误向上传播。"""
    try:
        memory_id = int(memory_id)
    except (TypeError, ValueError):
        return {"status": "not_found"}

    doc = await memory_engine.get_memory_for_recall(memory_id)
    if not doc:
        return {"memory_id": memory_id, "status": "not_found"}
    metadata = doc["metadata"]
    if (
        (
            recall_session_id is not None
            and metadata.get("session_id") != recall_session_id
        )
        or (
            recall_persona_id is not None
            and metadata.get("persona_id") != recall_persona_id
        )
        or str(metadata.get("status") or "active") != "active"
    ):
        return {"memory_id": memory_id, "status": "not_found"}

    facts = await memory_engine.resolve_live_facts(memory_id, doc["text"], metadata)
    if facts is None:
        return {
            "memory_id": memory_id,
            "status": "lifecycle_limited",
            "facts": [],
            "message": "memory is fully expired or forgotten",
        }
    result: dict[str, Any] = {
        "memory_id": memory_id,
        "status": "success",
        "facts": facts,
        "source_status": "not_requested",
        "source_messages": [],
    }
    if not include_source:
        return result

    # 无法从剩余原子证明已被清理的事实不在原文里，原子化原文统一保守拒绝。
    atom_backed = bool(metadata.get("atom_types")) or (
        await memory_engine.count_memory_atoms(memory_id) > 0
    )
    if atom_backed:
        result.update(
            source_status="lifecycle_limited",
            message="source withheld: atomized memories cannot prove full-source visibility",
        )
        return result
    if not metadata.get("has_source"):
        result["source_status"] = "source_unavailable"
        return result
    try:
        raw_source = await memory_engine.get_memory_source(memory_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result.update(
            source_status="timeout"
            if isinstance(exc, asyncio.TimeoutError)
            else "error",
            partial=True,
            message="retained source reading failed",
        )
        return result
    if not raw_source:
        result["source_status"] = "source_unavailable"
        return result
    result["source_status"] = "ok"
    for item in raw_source:
        if isinstance(item, dict):
            result["source_messages"].append(
                {
                    "role": str(item.get("role") or ""),
                    "sender_id": str(item.get("sender_id") or ""),
                    "timestamp": item.get("timestamp"),
                    "content": str(item.get("content") or ""),
                }
            )
        else:
            result["source_messages"].append(
                {
                    "role": "",
                    "sender_id": "",
                    "timestamp": None,
                    "content": str(item),
                }
            )
    return result
