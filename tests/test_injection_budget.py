"""Tests for injection budget allocation and truncation utilities."""

from astrbot_plugin_livingmemory.core.utils.injection_budget import (
    allocate_char_budgets,
    estimate_chars,
    truncate_display_text,
)


class TestEstimateChars:
    def test_empty(self):
        assert estimate_chars("") == 0.0

    def test_pure_chinese(self):
        assert estimate_chars("你好世界") == 4.0

    def test_pure_ascii(self):
        # 每个 ASCII 字符计 0.25
        assert estimate_chars("hello") == 1.25

    def test_mixed(self):
        # 2 个中文 + 4 个英文 = 2 + 1 = 3.0
        assert estimate_chars("你好abcd") == 3.0

    def test_cjk_punctuation_counts_full(self):
        assert estimate_chars("。！？") == 3.0


class TestTruncateDisplayText:
    def test_short_text_returned_as_is(self):
        text = "今天天气不错。我们去公园散步了。"
        assert truncate_display_text(text, 100.0) == text

    def test_exact_budget_not_truncated(self):
        text = "你好世界"  # 恰好 4.0 额度
        assert truncate_display_text(text, 4.0) == text

    def test_truncates_at_sentence_boundary(self):
        text = "第一句话。第二句话。第三句话很长很长很长很长。"
        result = truncate_display_text(text, 12.0)
        assert result.endswith("…")
        # 在句子边界截断，保留完整句子
        assert result.startswith("第一句话。")
        assert "第三句话" not in result

    def test_truncation_within_budget(self):
        text = "这是一段很长的记忆内容。" * 20
        budget = 15.0
        result = truncate_display_text(text, budget)
        assert estimate_chars(result) <= budget

    def test_hard_cut_without_boundary(self):
        text = "没有任何标点符号的很长很长的中文文本内容"
        result = truncate_display_text(text, 6.0)
        assert result.endswith("…")
        assert estimate_chars(result) <= 6.0

    def test_tiny_budget_returns_ellipsis(self):
        assert truncate_display_text("很长的正文", 0.0) == "…"

    def test_newline_is_boundary(self):
        text = "第一行内容\n第二行内容\n第三行内容"
        result = truncate_display_text(text, 8.0)
        assert result.endswith("…")
        assert result.startswith("第一行内容\n")


class TestAllocateCharBudgets:
    def test_empty_input(self):
        assert allocate_char_budgets([], [], 1000, 100, 300) == []

    def test_total_zero_disables_truncation(self):
        # total<=0 时原样返回估算长度
        result = allocate_char_budgets([0.9, 0.5], [800.0, 300.0], 0, 250, 600)
        assert result == [800, 300]

    def test_short_memories_pass_through(self):
        # 两条都短于保底额度：全额放行
        result = allocate_char_budgets([0.9, 0.5], [100.0, 80.0], 1500, 250, 600)
        assert result == [100, 80]

    def test_equal_scores_split_evenly(self):
        result = allocate_char_budgets(
            [1.0, 1.0], [1000.0, 1000.0], 1000, 0, 10000
        )
        assert sum(result) <= 1000 + 1e-6
        assert abs(result[0] - result[1]) <= 1

    def test_higher_score_gets_more(self):
        result = allocate_char_budgets(
            [1.0, 0.0], [1000.0, 1000.0], 900, 0, 10000
        )
        assert result[0] > result[1]

    def test_max_per_memory_cap(self):
        result = allocate_char_budgets(
            [1.0, 1.0], [5000.0, 5000.0], 3000, 0, 600
        )
        assert all(b <= 600 + 1e-9 for b in result)

    def test_min_per_memory_floor(self):
        # 一条高分一条低分，低分也应有保底额度
        result = allocate_char_budgets(
            [1.0, 0.1], [1000.0, 1000.0], 1500, 250, 1000
        )
        assert result[1] >= 250

    def test_floors_capped_by_actual_length(self):
        # 短记忆不会被保底额度抬高超过自身长度
        result = allocate_char_budgets([0.5, 0.5], [100.0, 800.0], 1500, 250, 600)
        assert result[0] == 100

    def test_sum_within_total_budget(self):
        scores = [0.9, 0.7, 0.5, 0.3, 0.2]
        lengths = [800.0, 600.0, 500.0, 400.0, 300.0]
        result = allocate_char_budgets(scores, lengths, 1500, 250, 600)
        assert sum(result) <= 1500 + 1e-6

    def test_negative_scores_treated_as_zero(self):
        result = allocate_char_budgets(
            [-0.5, -0.1], [1000.0, 1000.0], 1000, 0, 10000
        )
        # 全零权重退化为均等分配
        assert sum(result) <= 1000 + 1e-6
        assert abs(result[0] - result[1]) <= 1

    def test_floors_exceed_total_falls_back_to_proportional(self):
        # Σfloor=750 > total=500：跳过保底，按比例分配
        result = allocate_char_budgets(
            [1.0, 1.0, 1.0], [1000.0, 1000.0, 1000.0], 500, 250, 10000
        )
        assert sum(result) <= 500 + 1e-6
        assert all(b > 0 for b in result)

    def test_surplus_redistributed_from_short_memories(self):
        # 第一条很短，其用不完的额度应流向第二条
        result_short_first = allocate_char_budgets(
            [0.5, 0.5], [50.0, 2000.0], 1000, 250, 10000
        )
        assert result_short_first[1] > 750
