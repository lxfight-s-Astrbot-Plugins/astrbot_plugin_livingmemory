"""Release workflow contract tests."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-release.yml"


def test_release_notes_keep_hashes_and_final_commit() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert '--pretty=tformat:"%s%x09%h%x09%an"' in workflow
    assert "while IFS=$'\\t' read -r subject hash author" in workflow
    assert "[${hash}](${commit_url})" in workflow
    assert "IFS='|||'" not in workflow


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
