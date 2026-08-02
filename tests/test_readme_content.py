"""Content contracts for the multilingual project overview."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README_NAMES = ("README.md", "README_en.md", "README_ru.md")
LANGUAGE_LINKS = README_NAMES
LOCAL_IMAGE_RE = re.compile(r'<img\s+[^>]*src="([^"]+)"', re.IGNORECASE)
EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0F"
    "\u20E3"
    "]"
)


def _read_readmes() -> dict[str, str]:
    return {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in README_NAMES
    }


def test_readmes_are_concise_and_emoji_free() -> None:
    for name, content in _read_readmes().items():
        assert len(content.splitlines()) <= 130, f"{name} is too long"
        assert not EMOJI_RE.search(content), f"{name} contains an emoji"


def test_readmes_link_languages_and_project_resources() -> None:
    required = (
        "CHANGELOG.md",
        "LICENSE",
        "/releases",
        "/issues",
        "qm.qq.com",
        "953245617",
        "`lxfight`",
        "astrbot_plugin_livingmemory",
    )
    for name, content in _read_readmes().items():
        assert "<h1>LivingMemory</h1>" in content
        for language_link in LANGUAGE_LINKS:
            if language_link != name:
                assert language_link in content
        for link in required:
            assert link in content, f"{name} is missing {link}"


def test_readme_local_images_exist() -> None:
    for name, content in _read_readmes().items():
        image_sources = LOCAL_IMAGE_RE.findall(content)
        local_sources = [src for src in image_sources if "://" not in src]
        assert local_sources, f"{name} has no local visual asset"
        for source in local_sources:
            assert (ROOT / source).is_file(), f"{name} references missing {source}"


def test_readmes_do_not_claim_a_3d_graph() -> None:
    for name, content in _read_readmes().items():
        outdated_claim = re.search(r"(?<![%\w])3D(?![%\w])", content)
        assert outdated_claim is None, f"{name} contains an outdated graph claim"


def test_legacy_chinese_readme_points_to_default_homepage() -> None:
    content = (ROOT / "README_zh.md").read_text(encoding="utf-8")

    assert "[README.md](README.md)" in content
