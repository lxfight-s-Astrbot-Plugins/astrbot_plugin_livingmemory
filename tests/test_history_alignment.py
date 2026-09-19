"""
Tests for history alignment (webui edit/regenerate revert detection).
"""

import pytest

from astrbot_plugin_livingmemory.core.utils.history_alignment import (
    compute_revert_cutoff,
)


def _row(role: str, checkpoint: str | None = None, idx: int = 0) -> dict:
    metadata = {"llm_checkpoint_id": checkpoint} if checkpoint else {}
    return {"id": idx, "role": role, "metadata": metadata}


def _turn(checkpoint: str, idx: int = 0) -> list[dict]:
    """一条完整轮次：user + assistant。"""
    return [
        _row("user", checkpoint, idx),
        _row("assistant", checkpoint, idx + 1),
    ]


def _rows(turns: list[str], idx_start: int = 0) -> list[dict]:
    rows = []
    index = idx_start
    for checkpoint in turns:
        rows.extend(_turn(checkpoint, index))
        index += 2
    return rows


class TestComputeRevertCutoff:
    def test_in_sync_returns_none(self):
        rows = _rows(["C1", "C2"])
        assert compute_revert_cutoff(rows, ["C1", "C2"]) is None

    def test_revert_last_turn(self):
        rows = _rows(["C1", "C2"])
        # C2 被回滚（LLM 历史只剩 C1）
        assert compute_revert_cutoff(rows, ["C1"]) == 2

    def test_revert_all_turns_with_empty_context(self):
        rows = _rows(["C1", "C2"])
        assert compute_revert_cutoff(rows, []) == 0

    def test_front_trim_only_keeps_head(self):
        # AstrBot 上下文压缩：C1 被裁剪但 C2 保留 → 不删除
        rows = _rows(["C1", "C2"])
        assert compute_revert_cutoff(rows, ["C2"]) is None

    def test_front_trim_plus_revert(self):
        # C1 被裁剪，C3 被回滚；仅删除 C3 轮次
        rows = _rows(["C1", "C2", "C3"])
        assert compute_revert_cutoff(rows, ["C2"]) == 4

    def test_store_gap_with_position_mismatch_is_tolerated(self):
        # LLM 历史含 store 中无对应 user 行的轮次（如插件曾被禁用）：
        # 不再中止对账（原"中段空洞"路径已放宽为向前回溯），无回滚尾部时返回 None
        rows = [*_turn("C1", 0), *_turn("C3", 2)]
        assert compute_revert_cutoff(rows, ["C1", "C2", "C3"]) is None

    def test_image_turn_gap_in_sync_is_kept(self):
        # 纯图片轮次：user 行从未入库，仅 assistant 行存在（C2）；
        # 对账不删除任何内容（LLM 历史与 store 均无回滚尾部）
        rows = [
            *_turn("C1", 0),
            _row("assistant", "C2", 2),
            *_turn("C3", 3),
        ]
        assert compute_revert_cutoff(rows, ["C1", "C2", "C3"]) is None

    def test_image_turn_gap_then_revert_still_detected(self):
        # 图片轮次（store 缺 user 行）之后回滚 C3：仍能正确识别回滚尾部
        rows = [
            *_turn("C1", 0),
            _row("assistant", "C2", 2),
            *_turn("C3", 3),
        ]
        assert compute_revert_cutoff(rows, ["C1", "C2"]) == 3

    def test_image_turn_itself_reverted(self):
        # 被回滚的图片轮次：删除其孤立的 assistant 行（user 行本就不存在）
        rows = [
            *_turn("C1", 0),
            _row("assistant", "C2", 2),
        ]
        assert compute_revert_cutoff(rows, ["C1"]) == 2

    def test_legacy_tail_without_checkpoint_returns_none(self):
        rows = [_row("user", "C1", 0), _row("assistant", None, 1)]
        assert compute_revert_cutoff(rows, ["C1"]) is None

    def test_legacy_head_with_new_tail_is_safe(self):
        rows = [
            _row("user", None, 0),
            _row("assistant", None, 1),
            *_turn("C2", 2),
        ]
        assert compute_revert_cutoff(rows, ["C2"]) is None

    def test_orphan_stale_assistant_cleaned(self):
        # 被取代轮次的迟到 bot 回复（孤立消息）应被清理
        rows = [_row("user", "C2", 0), _row("assistant", "C1", 1)]
        assert compute_revert_cutoff(rows, ["C2"]) == 1

    def test_empty_store_returns_none(self):
        assert compute_revert_cutoff([], ["C1"]) is None

    def test_empty_context_with_legacy_rows_returns_none(self):
        rows = [_row("user", None, 0)]
        assert compute_revert_cutoff(rows, []) is None

    def test_assistant_without_checkpoint_in_match_region_returns_none(self):
        rows = [_row("user", "C1", 0), _row("assistant", None, 1)]
        assert compute_revert_cutoff(rows, ["C1"]) is None
