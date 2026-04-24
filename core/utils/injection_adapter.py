"""
注入策略适配层

按 Provider/模型自动选择记忆注入策略，将兼容性降级规则与业务逻辑解耦。
"""

from typing import Any

from astrbot.api import logger


class InjectionAdapter:
    """根据 Provider/模型自动选择记忆注入策略的适配层。"""

    # 降级规则表：按 provider_type / model_name 匹配，执行注入方式降级
    _RULES: list[dict[str, Any]] = [
        {
            "provider_types": ["googlegenai_chat_completion"],
            "model_patterns": ["gemini"],
            "downgrades": {"fake_tool_call": "user_message_before"},
        },
    ]

    def resolve(self, provider: Any, configured_mode: str) -> tuple[str, str | None]:
        """
        根据当前 Provider 解析最终使用的注入模式。

        Args:
            provider: AstrBot 的 provider 实例
            configured_mode: 用户在配置中指定的注入方式

        Returns:
            (resolved_mode, fallback_reason)
            - resolved_mode: 实际使用的注入方式
            - fallback_reason: 降级原因描述；未降级时为 None
        """
        if configured_mode != "fake_tool_call":
            return configured_mode, None

        try:
            provider_type, model_name = self._extract_provider_info(provider)
        except Exception:
            return configured_mode, None

        for rule in self._RULES:
            if self._matches_rule(rule, provider_type, model_name):
                downgrade = rule["downgrades"].get(configured_mode)
                if downgrade:
                    reason = (
                        f"fake_tool_call is not fully compatible with "
                        f"Gemini (type={provider_type}, model={model_name})"
                    )
                    logger.warning(
                        "[LivingMemory] " + reason + f", fallback to {downgrade}."
                    )
                    return downgrade, reason

        return configured_mode, None

    @staticmethod
    def _extract_provider_info(provider: Any) -> tuple[str, str]:
        """从 provider 对象中提取 provider_type 和 model_name。"""
        provider_type = ""
        model_name = ""

        if provider is None:
            return provider_type, model_name

        config = getattr(provider, "provider_config", {})
        provider_type = str(config.get("type", "")) if isinstance(config, dict) else ""
        raw_model = (
            provider.get_model() if hasattr(provider, "get_model") else ""
        )
        model_name = str(raw_model) if raw_model is not None else ""

        return provider_type, model_name

    @staticmethod
    def _matches_rule(
        rule: dict[str, Any], provider_type: str, model_name: str
    ) -> bool:
        """判断当前 provider 是否命中某条降级规则。"""
        type_match = provider_type in rule.get("provider_types", [])
        model_match = any(
            pat.lower() in model_name.lower()
            for pat in rule.get("model_patterns", [])
        )
        return type_match or model_match
