"""
注入策略适配层

将历史配置的注入方式映射到唯一的 add-only 注入方式（extra_user_content），
废弃模式的兼容降级规则与业务逻辑解耦。

add-only 原则：插件对 LLM 请求只做"追加"一种写操作——把记忆作为
mark_as_temp 临时片段追加到当前用户消息末尾，由 AstrBot 在保存对话
历史时过滤。改写 req.prompt 或向 req.contexts 写入消息的方式都会被
AstrBot 持久化进对话历史，因此全部废弃。
"""

from __future__ import annotations


class InjectionAdapter:
    """把旧版注入配置映射到唯一 add-only 注入方式的适配层。"""

    # 唯一受支持的注入方式：追加临时片段，不污染对话历史
    SUPPORTED_MODE = "extra_user_content"

    # 已废弃的注入方式 → (回退方式, 废弃原因)
    _DEPRECATED_MODES: dict[str, tuple[str, str]] = {
        "system_prompt": (
            "extra_user_content",
            "system_prompt 已废弃（严重破坏 LLM 前缀缓存），自动回退至 extra_user_content",
        ),
        "user_message_before": (
            "extra_user_content",
            "user_message_before 已废弃（会改写用户消息并持久化进对话历史），"
            "自动回退至 extra_user_content",
        ),
        "user_message_after": (
            "extra_user_content",
            "user_message_after 已废弃（改写用户消息并持久化进对话历史），"
            "自动回退至 extra_user_content",
        ),
        "fake_tool_call": (
            "extra_user_content",
            "fake_tool_call 已废弃（伪造消息对会持久化进对话历史），"
            "自动回退至 extra_user_content",
        ),
        "fake_tool_call_deepseek_v4": (
            "extra_user_content",
            "fake_tool_call_deepseek_v4 已废弃（伪造消息对会持久化进对话历史），"
            "自动回退至 extra_user_content",
        ),
    }

    def resolve(self, configured_mode: str) -> tuple[str, str | None]:
        """
        解析最终使用的注入模式。

        Args:
            configured_mode: 用户在配置中指定的注入方式

        Returns:
            (resolved_mode, fallback_reason)
            - resolved_mode: 实际使用的注入方式（仅 extra_user_content）
            - fallback_reason: 废弃/未知模式的回退原因；未回退时为 None
        """
        mode = str(configured_mode or "").strip()
        if not mode:
            return self.SUPPORTED_MODE, None
        if mode == self.SUPPORTED_MODE:
            return mode, None

        deprecated = self._DEPRECATED_MODES.get(mode)
        if deprecated is not None:
            return deprecated

        return (
            self.SUPPORTED_MODE,
            f"未知注入方式 {mode}，自动回退至 extra_user_content",
        )


__all__ = ["InjectionAdapter"]
