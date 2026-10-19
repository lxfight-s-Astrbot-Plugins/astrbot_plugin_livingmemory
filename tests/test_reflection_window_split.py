"""#255 总结窗口切分：按本地日期变化或空闲间隔选择最老片段。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
    MemoryReflection,
)


def _day(day: int, hour: int, minute: int = 0) -> float:
    """构造 2025-03 某天某时刻的本地时间戳（与切分判断使用同一时区）。"""
    return datetime(2025, 3, day, hour, minute).timestamp()


def _messages(*timestamps) -> list[SimpleNamespace]:
    return [SimpleNamespace(timestamp=timestamp) for timestamp in timestamps]


def _find(messages, *, split_by_day: bool = True, gap_minutes: int = 180):
    return MemoryReflection._find_window_split_boundary(
        messages, split_by_day=split_by_day, gap_minutes=gap_minutes
    )


@pytest.mark.parametrize(
    ("case", "timestamps", "split_by_day", "gap_minutes", "expected"),
    [
        # 同一天、无长间隔：不切分，保持原有整段总结
        (
            "same-day-no-gap",
            [_day(1, 9, 0), _day(1, 9, 5), _day(1, 9, 10), _day(1, 9, 15)],
            True,
            180,
            None,
        ),
        # 跨过本地午夜：在日期第一次变化的相邻消息处切分
        (
            "midnight-crossing",
            [_day(1, 23, 0), _day(1, 23, 5), _day(1, 23, 55), _day(2, 0, 1)],
            True,
            180,
            (3, "day"),
        ),
        # 间隔 181 分钟 > 阈值 180：按空闲间隔切分
        (
            "gap-just-over-threshold",
            [_day(1, 9, 0), _day(1, 9, 1), _day(1, 9, 2), _day(1, 12, 3)],
            True,
            180,
            (3, "gap"),
        ),
        # 间隔 179 分钟 < 阈值：不切分
        (
            "gap-just-under-threshold",
            [_day(1, 9, 0), _day(1, 9, 1), _day(1, 9, 2), _day(1, 12, 1)],
            True,
            180,
            None,
        ),
        # 间隔恰好等于阈值：严格大于才切分
        (
            "gap-exactly-threshold",
            [_day(1, 9, 0), _day(1, 9, 1), _day(1, 9, 2), _day(1, 12, 2)],
            True,
            180,
            None,
        ),
        # gap_minutes=0 关闭空闲间隔切分（同一天的长间隔不切分）
        (
            "gap-disabled",
            [_day(1, 9, 0), _day(1, 9, 1), _day(1, 9, 2), _day(1, 15, 2)],
            True,
            0,
            None,
        ),
        # gap_minutes=0 只关闭空闲间隔，跨天仍然切分
        (
            "gap-disabled-still-splits-by-day",
            [_day(1, 23, 0), _day(1, 23, 1), _day(1, 23, 2), _day(2, 1, 2)],
            True,
            0,
            (3, "day"),
        ),
        # split_by_day=False 时同一天的长间隔仍可按 gap 切分
        (
            "day-disabled-still-splits-by-gap",
            [_day(1, 9, 0), _day(1, 9, 1), _day(1, 9, 2), _day(1, 12, 3)],
            False,
            180,
            (3, "gap"),
        ),
        # 空消息列表：不切分
        ("empty", [], True, 180, None),
        # 单条消息：不足一轮，不切分
        ("single-message", [_day(1, 12, 0)], True, 180, None),
        # 两条消息：最老片段不足 2 条，不切分
        ("two-messages", [_day(1, 23, 0), _day(2, 1, 0)], True, 180, None),
        # 时间戳全相同：不切分且不报错
        (
            "equal-timestamps",
            [_day(1, 9, 0), _day(1, 9, 0), _day(1, 9, 0), _day(1, 9, 0)],
            True,
            180,
            None,
        ),
        # 乱序时间戳：更早的当前消息保守跳过，仍在首个有效日期边界切分
        (
            "unsorted-timestamps",
            [_day(1, 23, 0), _day(1, 23, 1), _day(2, 0, 30), _day(2, 0, 29)],
            True,
            180,
            (2, "day"),
        ),
        # 缺失/非法时间戳：不参与切分，不报错
        (
            "invalid-timestamps",
            [None, "not-a-timestamp", _day(1, 9, 0), _day(1, 9, 1)],
            True,
            180,
            None,
        ),
        # 首个边界只切出 1 条消息（不足一轮）时跳过，取下一个有效边界
        (
            "skip-too-short-first-segment",
            [_day(1, 23, 0), _day(2, 0, 1), _day(2, 12, 0), _day(2, 12, 1)],
            True,
            180,
            (2, "gap"),
        ),
    ],
    ids=[
        "same-day-no-gap",
        "midnight-crossing",
        "gap-just-over-threshold",
        "gap-just-under-threshold",
        "gap-exactly-threshold",
        "gap-disabled",
        "gap-disabled-still-splits-by-day",
        "day-disabled-still-splits-by-gap",
        "empty",
        "single-message",
        "two-messages",
        "equal-timestamps",
        "unsorted-timestamps",
        "invalid-timestamps",
        "skip-too-short-first-segment",
    ],
)
def test_find_window_split_boundary(
    case, timestamps, split_by_day, gap_minutes, expected
):
    assert (
        _find(
            _messages(*timestamps),
            split_by_day=split_by_day,
            gap_minutes=gap_minutes,
        )
        == expected
    )


def test_find_window_split_boundary_returns_oldest_boundary_only():
    """多个边界并存时只返回最老的一个，较新片段留待后续触发（FIFO）。"""
    messages = _messages(
        _day(1, 22, 0),
        _day(1, 22, 5),
        _day(1, 23, 0),
        _day(2, 0, 5),
        _day(2, 0, 10),
        _day(2, 8, 0),
    )

    assert _find(messages) == (3, "day")
