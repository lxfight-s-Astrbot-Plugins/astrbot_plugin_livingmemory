"""Run the LivingMemory smoke test suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SMOKE_TARGETS = [
    "tests/smoke/test_graph_memory_smoke.py",
    "tests/integration/test_full_workflow.py::test_recall_reflection_and_search_workflow",
    "tests/integration/test_real_db_end_to_end.py::test_normal_message_pipeline_with_real_database",
    "tests/integration/test_real_db_end_to_end.py::test_recall_injection_with_real_database",
]


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    cmd = ["uv", "run", "pytest", *SMOKE_TARGETS, *sys.argv[1:]]
    print("Running smoke suite:")
    for target in SMOKE_TARGETS:
        print(f"- {target}")
    completed = subprocess.run(cmd, cwd=plugin_root)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
