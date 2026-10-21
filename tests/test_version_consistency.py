"""Guard against version strings drifting between the files that hardcode them."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"


def _metadata_version() -> str:
    match = re.search(r"^version:\s*(\S+)\s*$", (ROOT / "metadata.yaml").read_text("utf-8"), re.M)
    assert match, "metadata.yaml 缺少 version 字段"
    return match.group(1)


def test_all_hardcoded_versions_match_metadata() -> None:
    expected = _metadata_version()

    main_py = (ROOT / "main.py").read_text("utf-8")
    assert "@register(" not in main_py, "main.py 不应再使用已废弃的 @register 装饰器"

    package = json.loads((ROOT / "package.json").read_text("utf-8"))
    assert package["version"] == expected, "package.json 版本未同步"

    lock = json.loads((ROOT / "package-lock.json").read_text("utf-8"))
    assert lock["version"] == expected, "package-lock.json 顶层版本未同步"
    assert lock["packages"][""]["version"] == expected, "package-lock.json 包版本未同步"

    from astrbot_plugin_livingmemory.core.managers.backup_manager import PLUGIN_VERSION

    assert PLUGIN_VERSION == expected, "backup_manager.PLUGIN_VERSION 未同步"


def test_bump_version_script_updates_every_hardcoded_file(tmp_path: Path) -> None:
    """scripts/bump_version.py 必须一次改全所有硬编码版本号。"""
    (tmp_path / "core" / "managers").mkdir(parents=True)
    (tmp_path / "metadata.yaml").write_text(
        "name: astrbot_plugin_livingmemory\nversion: 1.2.3\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "version": "1.2.3"}), encoding="utf-8"
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "x",
                "version": "1.2.3",
                "packages": {"": {"name": "x", "version": "1.2.3"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "core" / "managers" / "backup_manager.py").write_text(
        'PLUGIN_VERSION = "1.2.3"\n', encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "1.3.0"],
        env={**os.environ, "LIVINGMEMORY_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    assert "1.3.0" in (tmp_path / "metadata.yaml").read_text("utf-8")
    assert json.loads((tmp_path / "package.json").read_text())["version"] == "1.3.0"
    lock = json.loads((tmp_path / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == "1.3.0"
    assert '"1.3.0"' in (
        tmp_path / "core" / "managers" / "backup_manager.py"
    ).read_text("utf-8")
