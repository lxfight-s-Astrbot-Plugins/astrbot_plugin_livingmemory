"""Backend i18n: Japanese translations are present and loadable."""

import json
from pathlib import Path

from astrbot_plugin_livingmemory.core import i18n_backend

ROOT = Path(__file__).resolve().parents[1]


def _flat_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _flat_keys(value, path)
        else:
            keys.add(path)
    return keys


def test_backend_ja_matches_zh_structure() -> None:
    zh = json.loads((ROOT / "core/i18n/zh.json").read_text(encoding="utf-8"))
    ja = json.loads((ROOT / "core/i18n/ja.json").read_text(encoding="utf-8"))
    assert _flat_keys(ja) == _flat_keys(zh)


def test_backend_ja_translations_load() -> None:
    i18n_backend.init("ja")
    try:
        assert i18n_backend.t("common.yes") == "はい"
        report = i18n_backend.t(
            "status.report",
            total=3,
            session_count=1,
            last_update="now",
            db_size=1.5,
        )
        assert "LivingMemory ステータスレポート" in report
        assert "3" in report
    finally:
        i18n_backend.init("zh")
