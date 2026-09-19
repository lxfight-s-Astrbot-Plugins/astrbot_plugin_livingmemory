"""证据流分页：按最终 JSON 成本决定字符边界与下一页位置。"""

from collections.abc import Callable
from typing import Any


def encode_stream_cursor(stream: str, index: int, char: int) -> str:
    return f"{stream}:{index}:{char}"


def decode_stream_cursor(cursor: str) -> tuple[str, int, int]:
    if not cursor:
        return "f", 0, 0
    parts = cursor.split(":")
    try:
        if len(parts) != 3 or parts[0] not in ("f", "s"):
            raise ValueError
        index, char = int(parts[1]), int(parts[2])
        if index < 0 or char < 0:
            raise ValueError
        return parts[0], index, char
    except ValueError as exc:
        raise ValueError("invalid cursor; pass next_cursor unchanged") from exc


def fitting_prefix(text: str, fits: Callable[[str], bool]) -> str:
    """二分测量实际序列化大小，包含引号、反斜杠等转义成本。"""
    if fits(text):
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(text[:middle]):
            low = middle
        else:
            high = middle - 1
    return text[:low]


def paginate_evidence(
    evidence: dict[str, Any],
    cursor: str,
    fits: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """返回可连续恢复的一页；fits 测量完整工具结果，而非仅证据正文。"""
    if evidence["status"] != "success":
        return dict(evidence)
    facts = evidence["facts"]
    sources = evidence["source_messages"]
    tag, index, char = decode_stream_cursor(cursor)
    if tag == "s" and evidence["source_status"] == "not_requested":
        raise ValueError("source cursors require include_source=true")
    items = facts if tag == "f" else sources
    if index > len(items) or (index == len(items) and char):
        raise ValueError("cursor is outside the current evidence; start a new read")
    stream = [("f", i, {}, text) for i, text in enumerate(facts)]
    stream.extend(("s", i, item, item["content"]) for i, item in enumerate(sources))
    position = index if tag == "f" else len(facts) + index
    if position < len(stream) and char > len(stream[position][3]):
        raise ValueError("cursor is outside the current evidence; start a new read")

    page = {
        **evidence,
        "facts": [],
        "source_messages": [],
        "facts_total": len(facts),
        "source_total": len(sources),
        "has_more": position < len(stream),
        "next_cursor": encode_stream_cursor(tag, index, char)
        if position < len(stream)
        else "",
        "truncated": position < len(stream),
    }
    # 可选说明不应挤掉小预算内的一整页正文，状态字段始终保留。
    if not fits(page):
        page.pop("message", None)

    for pos in range(position, len(stream)):
        stream_tag, item_index, metadata, text = stream[pos]
        offset = char if pos == position else 0
        remaining = text[offset:]
        output = page["facts"] if stream_tag == "f" else page["source_messages"]
        output.append("" if stream_tag == "f" else dict(metadata))

        def set_chunk(chunk: str) -> None:
            if stream_tag == "f":
                output[-1] = chunk
            else:
                output[-1] = {**metadata, "content": chunk}
            consumed = offset + len(chunk)
            if consumed < len(text):
                next_position = encode_stream_cursor(stream_tag, item_index, consumed)
            elif pos + 1 < len(stream):
                next_tag, next_index, _, _ = stream[pos + 1]
                next_position = encode_stream_cursor(next_tag, next_index, 0)
            else:
                next_position = ""
            page.update(
                has_more=bool(next_position),
                next_cursor=next_position,
                truncated=bool(next_position),
            )

        def fits_chunk(chunk: str) -> bool:
            set_chunk(chunk)
            return fits(page)

        chunk = fitting_prefix(remaining, fits_chunk)
        set_chunk(chunk)
        if not fits(page) or (remaining and not chunk):
            output.pop()
            page.update(
                has_more=True,
                next_cursor=encode_stream_cursor(stream_tag, item_index, offset),
                truncated=True,
            )
            return page
        if len(chunk) < len(remaining):
            return page
    page.update(has_more=False, next_cursor="", truncated=False)
    return page
