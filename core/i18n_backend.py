"""
Backend i18n module for bot command responses.
Loads JSON translation files from core/i18n/ directory.
"""

import json
from pathlib import Path

from astrbot.api import logger

_fallback: dict = {}
_translations: dict = {}
_current_lang: str = "zh"


def init(language: str = "zh"):
    """Initialize translations. Fallback always loaded as zh."""
    global _fallback, _translations, _current_lang
    if not language or language not in ("zh", "en", "ru"):
        language = "zh"
    _current_lang = language
    base = Path(__file__).parent / "i18n"

    # Load fallback (zh)
    fallback_path = base / "zh.json"
    try:
        with open(fallback_path, encoding="utf-8") as f:
            _fallback = json.load(f)
    except Exception as exc:
        logger.error(f"Failed to load fallback i18n zh.json: {exc}")
        _fallback = {}

    # Load target language
    target_path = base / f"{language}.json"
    if target_path.exists():
        try:
            with open(target_path, encoding="utf-8") as f:
                _translations = json.load(f)
        except Exception as exc:
            logger.error(f"Failed to load i18n {language}.json: {exc}")
            _translations = _fallback
    else:
        logger.warning(f"i18n file not found for {language}, falling back to zh")
        _translations = _fallback


def _get(data: dict, key: str):
    parts = key.split(".")
    for part in parts:
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def t(key: str, **kwargs) -> str:
    """Get translated string by dot-notation key."""
    value = _get(_translations, key)
    if value is None:
        value = _get(_fallback, key)
    if value is None:
        logger.warning(f"i18n key missing: {key}")
        return key
    if not isinstance(value, str):
        return str(value)
    try:
        return value.format(**kwargs)
    except Exception as exc:
        logger.warning(f"i18n format error for key '{key}': {exc}")
        return value


def t_list(key: str) -> list[str]:
    """Get translated list of strings by dot-notation key."""
    value = _get(_translations, key)
    if value is None:
        value = _get(_fallback, key)
    if value is None:
        logger.warning(f"i18n list key missing: {key}")
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
