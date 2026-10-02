"""Beta 候选输出：总 JSON 配额、摘要预览与原文续读共享一个预算。"""

from typing import Any

from .agentic_recall_common import bounded_status, json_result
from .memory_evidence_output import fitting_prefix, paginate_evidence

_COMPACT_FIELDS = {"id", "memory_id", "content", "match_basis", "truncated"}
_SOURCE_FIELDS = {"source_status", "source_messages", "has_more", "next_cursor"}


def format_search_result(
    outcome: Any,
    *,
    query: str,
    target: str,
    filters: dict,
    budget: Any,
    evidence_by_id: dict[int, dict],
    include_source: bool,
) -> str:
    max_chars = budget.max_result_chars
    candidates = []
    contents = {}
    messages = [outcome.message] if outcome.message else []
    partial = outcome.partial
    for memory in outcome.results:
        metadata = memory.metadata
        evidence = evidence_by_id.get(memory.doc_id, {})
        if evidence.get("status") in ("not_found", "lifecycle_limited"):
            partial = True
            messages.append("some candidates are no longer visible")
            continue
        content = (
            "\n".join(evidence["facts"])
            if evidence.get("status") == "success"
            else str(memory.content or "")
        )
        contents[memory.doc_id] = content
        item = {
            "id": memory.doc_id,
            "memory_id": memory.doc_id,
            "content": content[:32],
            "truncated": len(content) > 32,
            "score": memory.final_score,
            **{
                key: metadata.get(key)
                for key in (
                    "importance",
                    "session_id",
                    "persona_id",
                    "create_time",
                    "last_access_time",
                    "source_time_start",
                    "source_time_end",
                )
            },
            "has_source": bool(metadata.get("has_source")),
            "match_basis": outcome.match_basis.get(memory.doc_id, ""),
        }
        if include_source:
            has_source_page = bool(evidence.get("source_messages"))
            source_status = evidence.get(
                "source_status", evidence.get("status", "error")
            )
            item.update(
                source_status=source_status,
                source_messages=[],
                has_more=has_source_page,
                next_cursor="s:0:0" if has_source_page else "",
            )
            if source_status in ("timeout", "error") or evidence.get("partial"):
                partial = True
                messages.append(
                    "some source reads timed out"
                    if source_status == "timeout"
                    else "some source reads failed"
                )
        candidates.append(item)

    payload = {
        "query": query,
        "target": target,
        "applied_filters": filters,
        "status": outcome.status
        if candidates
        else ("no_candidates" if outcome.status == "success" else outcome.status),
        "identity_status": outcome.identity_status,
        "count": 0,
        "has_more": outcome.has_more,
        "next_cursor": outcome.next_cursor,
        "remaining_budget": budget.to_status(),
        "results": [],
    }
    if outcome.coverage:
        payload["coverage"] = outcome.coverage
    if partial:
        payload["partial"] = True
    if messages:
        payload["message"] = "; ".join(dict.fromkeys(messages))
    if not candidates:
        if len(json_result(payload)) > max_chars:
            payload.pop("applied_filters", None)
            payload.pop("identity_status", None)
        return bounded_status(payload, max_chars)

    def select_page(items: list[dict]) -> None:
        payload["results"] = items
        payload["count"] = len(items)
        shortened = len(items) < len(candidates)
        payload["has_more"] = outcome.has_more or shortened
        payload["next_cursor"] = (
            outcome.result_cursors.get(items[-1]["id"], "")
            if shortened and items
            else outcome.next_cursor
        )

    # 优先保留正常字段；小预算装不下一条时才省略可选元数据。
    selected = []
    for compact in (False, True):
        pool = candidates
        if compact:
            payload.pop("query", None)
            payload.pop("applied_filters", None)
            payload.pop("identity_status", None)
            payload["compact"] = True
            if "message" in payload:
                payload["message"] = payload["message"][:80]
            pool = [
                {
                    key: value
                    for key, value in item.items()
                    if key in _COMPACT_FIELDS or key in _SOURCE_FIELDS
                }
                for item in candidates
            ]
        for count in range(len(pool), 0, -1):
            select_page(pool[:count])
            if len(json_result(payload)) <= max_chars:
                selected = payload["results"]
                break
        if selected:
            break
    if not selected:
        return bounded_status(
            {
                "status": "result_limit",
                "count": 0,
                "results": [],
                "message": "increase the result character budget to read these candidates",
                "remaining_budget": budget.to_status(),
            },
            max_chars,
        )

    source_items = [
        item
        for item in selected
        if evidence_by_id.get(item["id"], {}).get("source_messages")
    ]
    for index, item in enumerate(selected):
        content = contents[item["id"]]
        current_size = len(json_result(payload))
        shares = len(selected) - index + len(source_items)
        allowance = current_size + max(0, max_chars - current_size) // shares

        def set_content(text: str) -> bool:
            item["content"] = text
            item["truncated"] = len(text) < len(content)
            return len(json_result(payload)) <= allowance

        prefix = fitting_prefix(content, set_content)
        set_content(prefix)

    for index, item in enumerate(source_items):
        current_size = len(json_result(payload))
        allowance = current_size + max(0, max_chars - current_size) // (
            len(source_items) - index
        )
        content_truncated = item["truncated"]

        def set_source(page: dict) -> bool:
            for key in _SOURCE_FIELDS:
                item[key] = page[key]
            item["truncated"] = content_truncated or page["truncated"]
            return len(json_result(payload)) <= allowance

        # 搜索已经返回事实摘要，来源配额从原文开始，不重复消耗在 facts 上。
        page = paginate_evidence(evidence_by_id[item["id"]], "s:0:0", set_source)
        set_source(page)
    return json_result(payload)
