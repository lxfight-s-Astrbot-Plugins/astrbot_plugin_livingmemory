"""
相对时间词解析器
将文本中的相对时间词（今天、明天、today、tomorrow 等）转换为绝对日期标注
支持简体中文、繁体中文、英文

使用单次扫描策略，避免长词组被短词规则重复匹配的问题。
"""

import re
from datetime import datetime, timedelta

# 每个 entry 的 key 用于在运行时映射到日期计算函数
# 格式: (pattern, key)
# key 编码规则: "类型:偏移量"
_ENTRY_DEFS = [
    # === 日（中文长词优先） ===
    (r"大前天", "day:-3"),
    (r"大后天|大後天", "day:+3"),
    (r"前天|前日", "day:-2"),
    (r"后天|后日|後天|後日", "day:+2"),
    (r"昨天|昨日", "day:-1"),
    (r"明天|明日", "day:+1"),
    (r"今天|今日", "day:0"),
    # === 日（英文长词优先） ===
    (r"the\s+day\s+before\s+yesterday", "day:-2"),
    (r"the\s+day\s+after\s+tomorrow", "day:+2"),
    (r"yesterday", "day:-1"),
    (r"tomorrow", "day:+1"),
    (r"today", "day:0"),
    # === 周（中文） ===
    (r"上周|上週|上星期|上礼拜|上禮拜", "week:-1"),
    (r"下周|下週|下星期|下礼拜|下禮拜", "week:+1"),
    (r"这周|這周|這週|这星期|這星期|这礼拜|這禮拜|本周|本週", "week:0"),
    # === 周（英文） ===
    (r"last\s+week", "week:-1"),
    (r"next\s+week", "week:+1"),
    (r"this\s+week", "week:0"),
    # === 月（中文） ===
    (r"上个月|上個月", "month:-1"),
    (r"下个月|下個月", "month:+1"),
    (r"这个月|這個月|本月", "month:0"),
    # === 月（英文） ===
    (r"last\s+month", "month:-1"),
    (r"next\s+month", "month:+1"),
    (r"this\s+month", "month:0"),
    # === 年（中文） ===
    (r"前年", "year:-2"),
    (r"去年", "year:-1"),
    (r"今年", "year:0"),
    (r"明年", "year:+1"),
    (r"后年|後年", "year:+2"),
    # === 年（英文） ===
    (r"last\s+year", "year:-1"),
    (r"this\s+year", "year:0"),
    (r"next\s+year", "year:+1"),
]

# 提取 key 列表（与 group index 一一对应）
_ENTRY_KEYS = [key for _, key in _ENTRY_DEFS]

# 编译联合正则
_COMBINED_REGEX = re.compile(
    "|".join(f"({pattern})" for pattern, _ in _ENTRY_DEFS),
    re.IGNORECASE,
)

def resolve_relative_time(text: str, now: datetime) -> str:
    """
    将文本中的相对时间词替换为绝对日期标注（支持简体中文、繁体中文、英文）。

    原词保留，括号内附加绝对日期。
    例如："明天下午开会" -> "明天(2026-02-20)下午开会"
    例如："tomorrow afternoon" -> "tomorrow(2026-02-20) afternoon"

    使用单次扫描联合正则，确保长词组优先匹配，避免重复标注。

    支持的相对时间词：

    【日】
    - 简体: 今天、今日、昨天、昨日、前天、前日、明天、明日、后天、后日、大前天、大后天
    - 繁体: 後天、後日、大後天
    - 英文: today, yesterday, tomorrow, the day before yesterday, the day after tomorrow

    【周】
    - 简体: 上周、上星期、上礼拜、下周、下星期、下礼拜、这周、这星期、这礼拜、本周
    - 繁体: 上禮拜、下禮拜、這周、這星期、這禮拜、本週、上週、下週
    - 英文: last week, next week, this week

    【月】
    - 简体: 上个月、下个月、这个月、本月
    - 繁体: 上個月、下個月、這個月
    - 英文: last month, next month, this month

    【年】
    - 简体: 去年、今年、明年、前年、后年
    - 繁体: 後年
    - 英文: last year, this year, next year

    Args:
        text: 用户输入的文本
        now: 当前时间（datetime 对象）

    Returns:
        替换后的文本，相对时间词后面会附加对应的绝对日期
    """
    today = now.date()
    weekday = today.weekday()
    week_start = today - timedelta(days=weekday)
    this_year = today.year
    this_month = today.month

    def _calc_month(year: int, month: int, offset: int) -> str:
        month += offset
        if month < 1:
            month += 12
            year -= 1
        elif month > 12:
            month -= 12
            year += 1
        return f"{year}-{month:02d}"

    def _resolve_key(key: str) -> str:
        kind, offset_str = key.split(":")
        offset = int(offset_str)
        if kind == "day":
            return (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        elif kind == "week":
            return (week_start + timedelta(weeks=offset)).strftime("%Y-%m-%d") + "起"
        elif kind == "month":
            return _calc_month(this_year, this_month, offset)
        else:  # year
            return str(this_year + offset)

    def _replacer(match: re.Match) -> str:
        matched_text = match.group(0)
        # 如果后面已经跟着括号标注，跳过避免重复标注
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos] == "(":
            return matched_text
        # lastindex 直接定位匹配的 group
        idx = match.lastindex
        if idx is None:
            return matched_text
        key = _ENTRY_KEYS[idx - 1]
        return f"{matched_text}({_resolve_key(key)})"

    return _COMBINED_REGEX.sub(_replacer, text)