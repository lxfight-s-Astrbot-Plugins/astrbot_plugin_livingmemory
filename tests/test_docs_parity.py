"""Guards that keep the Chinese docs a complete translation of the English docs.

Structural counts and inline identifiers are translation-invariant, so they
catch a section silently dropped or added on only one side.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

PAIRS = [
    ("index.md", "en/index.md"),
    ("guide/getting-started.md", "en/guide/getting-started.md"),
    ("features.md", "en/features.md"),
    ("configuration.md", "en/configuration.md"),
    ("commands.md", "en/commands.md"),
    ("webui.md", "en/webui.md"),
    ("architecture.md", "en/architecture.md"),
    ("deployment.md", "en/deployment.md"),
    ("releases.md", "en/releases.md"),
]


def _read(relative: str) -> str:
    return (DOCS / relative).read_text(encoding="utf-8")


def _table_rows(text: str) -> int:
    return len(re.findall(r"^\|", text, re.M))


def _bullets(text: str) -> int:
    return len(re.findall(r"^\s*[-*] ", text, re.M))


def _fences(text: str) -> int:
    return len(re.findall(r"^```", text, re.M))


def _inline_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"`([^`\n]+)`", text)
        if re.fullmatch(r"[A-Za-z0-9_.\-]{2,}", token)
    }


def _han(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))


def test_docs_keep_structural_parity_across_languages() -> None:
    for zh_name, en_name in PAIRS:
        zh = _read(zh_name)
        en = _read(en_name)
        assert _table_rows(zh) == _table_rows(en), f"{zh_name}: 表格行数不一致"
        assert _bullets(zh) == _bullets(en), f"{zh_name}: 列表项数量不一致"
        assert _fences(zh) == _fences(en), f"{zh_name}: 代码块数量不一致"


def test_chinese_docs_cover_english_inline_identifiers() -> None:
    for zh_name, en_name in PAIRS:
        missing = _inline_tokens(_read(en_name)) - _inline_tokens(_read(zh_name))
        assert not missing, f"{zh_name}: 中文文档缺少英文侧标识符 {sorted(missing)}"


def test_chinese_translation_length_stays_in_expected_range() -> None:
    for zh_name, en_name in PAIRS:
        ratio = _han(_read(zh_name)) / max(1, _words(_read(en_name)))
        assert 0.9 <= ratio <= 2.2, f"{zh_name}: 中英文篇幅比例异常 ({ratio:.2f})"
