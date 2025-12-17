"""
配置管理器
集中管理插件配置的加载、验证和访问
"""

from typing import Any

from .config_validator import get_default_config, merge_config_with_defaults, validate_config
from .exceptions import ConfigurationError


class ConfigManager:
    """配置管理器"""

    def __init__(self, user_config: dict[str, Any] | None = None):
        """
        初始化配置管理器

        Args:
            user_config: 用户提供的配置字典
        """
        self._raw_config = user_config or {}
        self._config: dict[str, Any] = {}
        self._config_obj = None
        self._load_config()

    def _load_config(self) -> None:
        """加载并验证配置"""
        try:
            # 合并默认配置
            merged_config = merge_config_with_defaults(self._raw_config)
            # 验证配置
            self._config_obj = validate_config(merged_config)
            self._config = self._config_obj.model_dump()
        except Exception as e:
            # 配置验证失败，使用默认配置
            try:
                self._config = get_default_config()
                self._config_obj = validate_config(self._config)
            except Exception as e2:
                raise ConfigurationError(f"加载默认配置失败: {e2}") from e2

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
    def webui_settings(self) -> dict[str, Any]:
        """WebUI设置"""
        return self.get_section("webui_settings")

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
