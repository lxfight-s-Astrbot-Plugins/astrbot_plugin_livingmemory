"""Offline recall-quality regression gate (pytest marker: ``eval``).

The harness uses synthetic memories plus a deterministic char-bigram embedding,
so it only guards ranking / relevance-threshold regressions. It must not be
read as a measurement of real semantic recall quality; the script docstring
documents that limitation in full.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.eval_recall import DEFAULT_FIXTURE, DEFAULT_TOLERANCE, run_eval

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_recall_golden_regression() -> None:
    """Compare current document-route ranking against the recorded baseline."""
    metrics, checks = await run_eval(
        DEFAULT_FIXTURE,
        baseline_path=BASELINE_PATH,
        tolerance=DEFAULT_TOLERANCE,
    )

    assert metrics["negative_queries"] >= 6
    assert metrics["negative_injection_rate"] == 0
    assert metrics["p95_ms"] < 500

    failed = [check for check in checks if not check["passed"]]
    assert not failed, failed


@pytest.mark.eval
def test_baseline_file_is_valid() -> None:
    """The committed baseline must expose every guarded metric."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for metric in ("recall@5", "mrr@10", "ndcg@5", "negative_injection_rate"):
        assert isinstance(baseline.get(metric), (int, float)), metric
