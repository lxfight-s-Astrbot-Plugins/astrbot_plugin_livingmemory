"""Release workflow contract tests."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-release.yml"


@pytest.mark.parametrize("has_section", [True, False])
def test_release_notes_preserve_pr_credits_and_reject_missing_section(
    tmp_path: Path, has_section: bool
) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["create-release"]["steps"]
    script = next(s["run"] for s in steps if s["name"] == "Generate changelog")
    source = "\n".join(script.splitlines()[1:-1])
    version = "2.7.0-beta.1"
    heading = version if has_section else "2.7.0-beta.2"
    credits = "- [#123](https://github.com/example/plugin/pull/123) by @contributor"
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{heading}] - 2026-09-07\n\n"
        f"Beta release notes.\n{credits}\n\n## [2.6.1]\nOld release notes.\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        env={
            **os.environ,
            "RELEASE_VERSION": version,
            "PREVIOUS_VERSION": "2.6.1",
            "RELEASE_REPOSITORY": "example/plugin",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    output = tmp_path / "release-notes.md"
    if not has_section:
        assert result.returncode != 0
        assert "Missing CHANGELOG.md section" in result.stderr
        assert not output.exists()
        return

    assert result.returncode == 0, result.stderr
    notes = output.read_text(encoding="utf-8")
    assert credits in notes
    assert "Old release notes" not in notes
    assert f"compare/2.6.1...{version}" in notes
    release = next(s for s in steps if s["name"] == "Create Release")
    assert release["with"]["target_commitish"] == "${{ github.sha }}"


def test_release_copy_has_no_emoji_and_uses_current_actions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "softprops/action-gh-release@v2" in workflow
    assert "🎉" not in workflow
    assert "📝" not in workflow
    assert "✨" not in workflow
    assert "🐛" not in workflow
    assert "📚" not in workflow
    assert "🔧" not in workflow
    assert "📌" not in workflow
    assert "✅" not in workflow
    assert "🔗" not in workflow


BUMP_SCRIPT = ROOT / "scripts" / "bump_version.py"


def test_version_bump_keeps_dependency_versions_in_lockfile(tmp_path: Path) -> None:
    """锁文件只改根版本：与旧插件版本同号的依赖版本不能被改写。"""
    (tmp_path / "metadata.yaml").write_text("version: 2.9.0\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{\n  "name": "plugin-docs",\n  "version": "2.9.0"\n}\n', encoding="utf-8"
    )
    lockfile = {
        "name": "plugin-docs",
        "version": "2.9.0",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "plugin-docs", "version": "2.9.0"},
            "node_modules/birpc": {
                "version": "2.9.0",
                "resolved": "https://example.invalid/birpc-2.9.0.tgz",
                "integrity": "sha512-test",
            },
        },
    }
    (tmp_path / "package-lock.json").write_text(
        json.dumps(lockfile, indent=2) + "\n", encoding="utf-8"
    )
    backup = tmp_path / "core" / "managers" / "backup_manager.py"
    backup.parent.mkdir(parents=True)
    backup.write_text('PLUGIN_VERSION = "2.9.0"\n', encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(BUMP_SCRIPT), "2.9.1"],
        env={**os.environ, "LIVINGMEMORY_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    bumped = json.loads((tmp_path / "package-lock.json").read_text(encoding="utf-8"))
    assert bumped["version"] == "2.9.1"
    assert bumped["packages"][""]["version"] == "2.9.1"
    assert bumped["packages"]["node_modules/birpc"]["version"] == "2.9.0"
    assert 'PLUGIN_VERSION = "2.9.1"' in backup.read_text(encoding="utf-8")
