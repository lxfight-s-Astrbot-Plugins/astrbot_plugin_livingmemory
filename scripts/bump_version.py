#!/usr/bin/env python3
"""Bump the LivingMemory plugin version in every file that hardcodes it.

Version sources: metadata.yaml, package.json, package-lock.json and
backup_manager.PLUGIN_VERSION. The plugin class intentionally has no
@register decorator (deprecated upstream), so main.py no longer carries
the version.

Usage: python scripts/bump_version.py 2.7.0
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(
    os.environ.get("LIVINGMEMORY_ROOT", Path(__file__).resolve().parents[1])
).resolve()
METADATA = ROOT / "metadata.yaml"


def current_version() -> str:
    match = re.search(r"^version:\s*(\S+)\s*$", METADATA.read_text("utf-8"), re.M)
    if not match:
        raise SystemExit("metadata.yaml 缺少 version 字段")
    return match.group(1)


def replace_in_file(path: Path, pattern: str, replacement: str, flags: int = 0) -> int:
    text = path.read_text("utf-8")
    updated, count = re.subn(pattern, replacement, text, flags=flags)
    if updated != text:
        path.write_text(updated, "utf-8")
    return count


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts/bump_version.py <new-version>")
    new = sys.argv[1].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", new):
        raise SystemExit(f"版本号格式不合法: {new}")
    old = current_version()
    if old == new:
        raise SystemExit(f"版本号未变化: {old}")

    escaped_old = re.escape(old)
    targets = [
        (METADATA, rf"(^version:\s*){escaped_old}\s*$", rf"\g<1>{new}", re.M),
        (
            ROOT / "package.json",
            rf'("version":\s*"){escaped_old}(")',
            rf"\g<1>{new}\g<2>",
            0,
        ),
        (
            ROOT / "package-lock.json",
            rf'("version":\s*"){escaped_old}(")',
            rf"\g<1>{new}\g<2>",
            0,
        ),
        (
            ROOT / "core" / "managers" / "backup_manager.py",
            rf'(PLUGIN_VERSION = "){escaped_old}(")',
            rf"\g<1>{new}\g<2>",
            0,
        ),
    ]

    changed: list[str] = []
    for path, pattern, replacement, flags in targets:
        count = replace_in_file(path, pattern, replacement, flags)
        if count:
            changed.append(f"{path.relative_to(ROOT).as_posix()} ({count} 处)")
        else:
            raise SystemExit(f"未在 {path.relative_to(ROOT)} 找到版本号 {old}")

    json.loads((ROOT / "package.json").read_text("utf-8"))
    json.loads((ROOT / "package-lock.json").read_text("utf-8"))
    print(f"版本已更新: {old} -> {new}")
    for item in changed:
        print("  " + item)
    print("请同步更新 CHANGELOG.md 与 docs/*/releases.md，然后执行测试。")


if __name__ == "__main__":
    main()
