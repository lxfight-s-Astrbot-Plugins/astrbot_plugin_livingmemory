"""Compatibility entry point for the real-storage recall benchmark."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "benchmark_recall.py"),
        run_name="__main__",
    )
