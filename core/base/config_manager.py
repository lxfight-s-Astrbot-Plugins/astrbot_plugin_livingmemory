"""
配置管理器
集中管理插件配置的加载、验证与访问
"""

import copy
from typing import Any

from astrbot.api import logger

from .config_validator import validate_config
from .exceptions import ConfigurationError


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
        self._legacy_injection_warned = False
        self._load_config()

    def _load_config(self) -> None:
        """按 Pydantic 模型校验配置；非法配置直接抛出 ConfigurationError。"""
        try:
            config = validate_config(self._raw_config)
        except Exception as exc:
            raise ConfigurationError(f"插件配置无效: {exc}") from exc
        self._config = config.model_dump()

        # 旧注入方式仅为配置兼容保留：运行时统一按 extra_user_content 处理，
        # 只在首次载入时提示一次，避免每次请求刷日志。
        injection_method = self._config["recall_engine"]["injection_method"]
        if injection_method != "extra_user_content":
            if not self._legacy_injection_warned:
                logger.warning(
                    "记忆注入方式 %s 已废弃，运行时按 extra_user_content 处理",
                    injection_method,
                )
                self._legacy_injection_warned = True
            self._config["recall_engine"]["injection_method"] = "extra_user_content"

    def apply_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist flat dotted-path config updates.

        The candidate config is validated against the Pydantic model before
        anything is written, so an invalid value is rejected instead of being
        clamped or silently discarded. Values are then deep-set into the host
        plugin config, saved with its ``save_config`` hook and reloaded.

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

        # 非法值在这里抛出 ValidationError（ValueError 子类），不写入宿主配置
        validated = validate_config(candidate).model_dump()

        for path in updates:
            parts = str(path).split(".")
            value: Any = validated
            for part in parts:
                value = value.get(part) if isinstance(value, dict) else None
            target = raw_config
            for part in parts[:-1]:
                child = target.get(part)
                if not isinstance(child, dict):
                    child = {}
                    target[part] = child
                target = child
            target[parts[-1]] = value

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
