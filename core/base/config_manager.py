"""
配置管理器
集中管理插件配置的加载、验证、修正和访问
"""

import copy
from typing import Any

from astrbot.api import logger

from .config_validator import (
    get_default_config,
    merge_config_with_defaults,
    normalize_config,
    validate_config,
)
from .exceptions import ConfigurationError


def _get_normalized_value(config: dict[str, Any], path: str) -> Any:
    """Read a dotted path from a normalized config dict."""
    current: Any = config
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class ConfigManager:
    """配置管理器"""

    def __init__(self, user_config: dict[str, Any] | None = None):
        """
        初始化配置管理器

        Args:
            user_config: 用户提供的配置字典
        """
        # Keep the host config object even when it is empty: it carries the
        # save_config hook used by the dashboard settings page.
        self._raw_config = user_config if user_config is not None else {}
        self._config: dict[str, Any] = {}
        self._config_obj = None
        self._load_config()

    def _load_config(self) -> None:
        """加载、修正并验证配置"""
        corrections: list[dict[str, Any]] = []
        try:
            # 合并默认配置
            merged_config = merge_config_with_defaults(self._raw_config)
            # 逐项修正超出允许范围的值（AstrBot 保存时只校验类型不校验范围）
            merged_config, corrections = normalize_config(merged_config)
            # 验证配置
            self._config_obj = validate_config(merged_config)
            self._config = self._config_obj.model_dump()
        except Exception:
            logger.warning("配置验证失败，已降级为默认配置", exc_info=True)
            # 配置验证失败，使用默认配置
            try:
                self._config = get_default_config()
                self._config_obj = validate_config(self._config)
            except Exception as e2:
                raise ConfigurationError(f"加载默认配置失败: {e2}") from e2

        if corrections:
            for item in corrections:
                logger.warning(
                    "配置项已自动修正 %s: %r -> %r (%s)",
                    item["path"],
                    item["old"],
                    item["new"],
                    item.get("reason", ""),
                )
            self._persist_normalized_config(corrections)

    def _persist_normalized_config(self, corrections: list[dict[str, Any]]) -> None:
        """把修正后的值写回 AstrBot 配置，使配置页显示修正后的数值。"""
        save_config = getattr(self._raw_config, "save_config", None)
        if not callable(save_config) or not isinstance(self._raw_config, dict):
            return
        try:
            for item in corrections:
                parts = item["path"].split(".")
                target: dict[str, Any] = self._raw_config
                for part in parts[:-1]:
                    child = target.get(part)
                    if not isinstance(child, dict):
                        break
                    target = child
                else:
                    target[parts[-1]] = item["new"]
            save_config()
            logger.info("已把 %d 项配置修正写回配置文件", len(corrections))
        except Exception as exc:
            logger.warning("配置修正写回失败，仅保留内存内修正: %s", exc)

    def apply_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist flat dotted-path config updates.

        The candidate config is merged with defaults, normalized and validated
        before anything is written, so an invalid value cannot wipe the rest of
        the configuration. Values are then deep-set into the host plugin config,
        saved with its ``save_config`` hook and reloaded.

        Args:
            updates: Mapping of dotted path (e.g. ``recall_engine.top_k``) to value.

        Returns:
            The reloaded configuration dict.

        Raises:
            ValueError: If the candidate configuration is invalid.
            RuntimeError: If the host config object is not writable.
        """
        if not isinstance(updates, dict) or not updates:
            raise ValueError("没有需要保存的配置项")
        raw_config = self._raw_config
        if not isinstance(raw_config, dict):
            raise RuntimeError("插件配置对象不可写")

        candidate = copy.deepcopy(dict(raw_config))
        for path, value in updates.items():
            parts = str(path).split(".")
            target = candidate
            for part in parts[:-1]:
                child = target.get(part)
                if not isinstance(child, dict):
                    child = {}
                    target[part] = child
                target = child
            target[parts[-1]] = value

        merged = merge_config_with_defaults(candidate)
        normalized, _ = normalize_config(merged)
        validate_config(normalized)

        for path in updates:
            parts = str(path).split(".")
            target = raw_config
            for part in parts[:-1]:
                child = target.get(part)
                if not isinstance(child, dict):
                    child = {}
                    target[part] = child
                target = child
            target[parts[-1]] = _get_normalized_value(normalized, path)

        save_config = getattr(raw_config, "save_config", None)
        if not callable(save_config):
            raise RuntimeError("当前宿主配置对象不支持保存")
        save_config()
        self._load_config()
        return self.get_all()

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置键，支持点号分隔的嵌套键（如 "provider_settings.llm_provider_id"）
            default: 默认值

        Returns:
            配置值
        """
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def get_section(self, section: str) -> dict[str, Any]:
        """
        获取配置节

        Args:
            section: 配置节名称

        Returns:
            配置节字典
        """
        return self._config.get(section, {})

    def get_all(self) -> dict[str, Any]:
        """获取所有配置"""
        return self._config.copy()

    @property
    def provider_settings(self) -> dict[str, Any]:
        """Provider设置"""
        return self.get_section("provider_settings")

    @property
    def session_manager(self) -> dict[str, Any]:
        """会话管理器配置"""
        return self.get_section("session_manager")

    @property
    def recall_engine(self) -> dict[str, Any]:
        """召回引擎配置"""
        return self.get_section("recall_engine")

    @property
    def reflection_engine(self) -> dict[str, Any]:
        """反思引擎配置"""
        return self.get_section("reflection_engine")

    @property
    def filtering_settings(self) -> dict[str, Any]:
        """过滤设置"""
        return self.get_section("filtering_settings")

    @property
    def graph_memory(self) -> dict[str, Any]:
        """Graph-memory settings."""
        return self.get_section("graph_memory")
