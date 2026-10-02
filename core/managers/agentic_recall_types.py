"""Beta 回忆的结果契约、来源时间与人物游标。"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..retrieval.hybrid_retriever import HybridResult


@dataclass
class AgenticRecallOutcome:
    results: list[HybridResult]
    status: str
    has_more: bool = False
    next_cursor: str = ""
    match_basis: dict[int, str] = field(default_factory=dict)
    identity_status: str = ""
    message: str = ""
    partial: bool = False
    coverage: str = ""
    result_cursors: dict[int, str] = field(default_factory=dict)


def normalize_doc_ids(values: Any) -> set[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_source_timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        try:
            timestamp = datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, OverflowError):
            return None
    if timestamp > 100_000_000_000:
        timestamp /= 1000.0
    return timestamp if math.isfinite(timestamp) and timestamp > 0 else None


def participant_time(metadata: dict[str, Any]) -> float:
    return parse_source_timestamp(metadata.get("source_time_start")) or 0.0


def encode_participant_cursor(timestamp: float, doc_id: int) -> str:
    return f"{timestamp!r}|{doc_id}"


def decode_participant_cursor(cursor: str) -> tuple[float, int] | None:
    if not cursor:
        return None
    timestamp, separator, doc_id = cursor.rpartition("|")
    try:
        position = float(timestamp), int(doc_id)
    except ValueError as exc:
        raise ValueError("invalid search_offset; pass next_cursor unchanged") from exc
    if not separator or not math.isfinite(position[0]) or position[1] < 1:
        raise ValueError("invalid search_offset; pass next_cursor unchanged")
    return position


def filter_source_time(
    results: list[HybridResult], start_ts: float | None, end_ts: float | None
) -> tuple[list[HybridResult], int]:
    if start_ts is None and end_ts is None:
        return results, 0
    kept = []
    unknown = 0
    for result in results:
        metadata = result.metadata
        start = parse_source_timestamp(metadata.get("source_time_start"))
        end = parse_source_timestamp(metadata.get("source_time_end")) or start
        if start is None and end is None:
            unknown += 1
            continue
        start = start if start is not None else end
        if start_ts is not None and end < start_ts:
            continue
        if end_ts is not None and start > end_ts:
            continue
        kept.append(result)
    return kept, unknown


def match_basis(result: HybridResult) -> str:
    breakdown = result.score_breakdown or {}
    parts = []
    if result.bm25_score is not None or breakdown.get("document_keyword_score"):
        parts.append("keyword")
    if result.vector_score is not None or breakdown.get("document_vector_score"):
        parts.append("vector")
    if breakdown.get("graph_keyword_score") or breakdown.get("graph_vector_score"):
        parts.append("graph")
    if "atom_score" in breakdown:
        parts.append("atom")
    return "+".join(parts) or "unknown"
