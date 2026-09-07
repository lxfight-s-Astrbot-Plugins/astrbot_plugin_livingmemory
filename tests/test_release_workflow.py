"""Release workflow contract tests."""

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
