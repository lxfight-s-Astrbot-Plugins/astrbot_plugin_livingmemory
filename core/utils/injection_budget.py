"""
injection_budget.py - 记忆注入预算工具

为记忆注入提供字符预算控制（见 Issue #242）：
- estimate_chars：混合中英文长度估算，中文字符计 1.0，其他字符计 0.25
- truncate_display_text：在句子边界处截断注入正文并追加省略号
- allocate_char_budgets：按召回权重在多条记忆间动态分配总预算

预算只约束注入正文（persona_summary）总和，元数据行与头尾文案不计入。
"""

from __future__ import annotations

# 截断后追加的省略号
ELLIPSIS = "…"

# 句子边界字符：截断时优先保留完整句子，提升注入内容可读性
_SENTENCE_BOUNDARIES = set("。！？!?；;…\n")


def _is_wide_char(char: str) -> bool:
    """判断是否为 CJK 字符 / 全角符号（约占 1 个字符额度）。"""
    return (
        "一" <= char <= "鿿"  # CJK 统一汉字
        or "　" <= char <= "〿"  # CJK 标点
        or "＀" <= char <= "￯"  # 全角形式
    )


def _is_ascii_word_char(char: str) -> bool:
    """判断是否为 ASCII 单词内字符（字母/数字）。"""
    return char.isascii() and char.isalnum()


def _backtrack_ascii_word(text: str, cut: int) -> int:
    """从 cut 向前回溯到 ASCII 单词边界，避免截断半个英文单词。"""
    if cut <= 0 or not _is_ascii_word_char(text[cut - 1]):
        return cut
    i = cut - 1
    while i > 0 and _is_ascii_word_char(text[i - 1]):
        i -= 1
    return i


def estimate_chars(text: str) -> float:
    """混合长度估算：CJK 字符计 1.0，其余字符计 0.25。

    英文等窄字符在 LLM token 与显示宽度上约占中文的 1/4，
    按此折算使预算对中英文记忆都大致对应实际占用。
    """
    if not text:
        return 0.0
    units = 0.0
    for char in text:
        units += 1.0 if _is_wide_char(char) else 0.25
    return units


def truncate_display_text(text: str, budget_units: float) -> str:
    """将正文截断到约 budget_units 额度以内。

    估算长度不超过预算时原样返回；否则在预算内前缀的最后一个
    句子边界（。！？!?；;… 及换行）处截断并追加省略号，
    前缀内无边界时硬截断。
    """
    if budget_units <= 0:
        return ELLIPSIS
    if estimate_chars(text) <= budget_units:
        return text

    # 预留省略号额度后计算可保留的前缀长度
    target = budget_units - estimate_chars(ELLIPSIS)
    units = 0.0
    cut = len(text)
    for i, char in enumerate(text):
        units += 1.0 if _is_wide_char(char) else 0.25
        if units > target:
            cut = i
            break

    prefix = text[:cut]
    boundary_pos = -1
    for i, char in enumerate(prefix):
        if char in _SENTENCE_BOUNDARIES:
            boundary_pos = i
    if boundary_pos >= 0:
        return prefix[: boundary_pos + 1] + ELLIPSIS
    if not prefix:
        return ELLIPSIS

    # 无句子边界时回溯到 ASCII 单词边界，避免把英文单词从中间截断
    cut = _backtrack_ascii_word(text, cut)
    prefix = text[:cut]
    if not prefix:
        return ELLIPSIS
    return prefix + ELLIPSIS


def allocate_char_budgets(
    scores: list[float],
    est_lengths: list[float],
    total: float,
    min_per: float,
    max_per: float,
) -> list[float]:
    """按召回权重为每条记忆分配字符额度。

    Args:
        scores: 各条记忆的召回权重（final_score），负值按 0 处理
        est_lengths: 各条记忆正文的估算长度（estimate_chars 结果）
        total: 总预算；<=0 时返回各条原始估算长度（即不截断）
        min_per: 单条保底额度；短于该值的记忆按实际长度放行
        max_per: 单条上限额度；短于该值的记忆按实际长度放行

    Returns:
        与输入顺序一致的每条记忆字符额度列表（浮点额度，供截断比较）。

    分配规则：先为每条预留保底额度（Σfloor 超过总预算时跳过保底），
    剩余额度按权重在未达上限的记忆间水填分配。
    """
    n = len(scores)
    if n == 0:
        return []
    if total <= 0:
        return [max(0.0, float(length)) for length in est_lengths]

    weights = [max(float(score), 0.0) for score in scores]
    if sum(weights) <= 0:
        weights = [1.0] * n

    caps = [min(float(est_lengths[i]), float(max_per)) for i in range(n)]
    floors = [min(float(min_per), caps[i]) for i in range(n)]

    budgets = list(floors) if sum(floors) <= total else [0.0] * n
    pool = total - sum(budgets)

    # 水填：剩余额度按权重分配，触顶的记忆退出后续分配
    while pool > 1e-9:
        active = [i for i in range(n) if budgets[i] < caps[i] - 1e-9]
        if not active:
            break
        weight_sum = sum(weights[i] for i in active)
        if weight_sum <= 0:
            break
        progressed = False
        for i in active:
            grant = min(pool * weights[i] / weight_sum, caps[i] - budgets[i])
            if grant > 1e-12:
                budgets[i] += grant
                progressed = True
        pool = total - sum(budgets)
        if not progressed:
            break

    return budgets
