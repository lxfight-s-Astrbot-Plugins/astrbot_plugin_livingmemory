"""
prompt_manager.py - 提示词管理器

集中管理插件所有可自定义的提示词模板。
- 内置默认值从 core/prompts/ 加载
- 用户自定义覆盖保存在 data/prompts/
- 内存缓存加速重复读取
- 单例模式确保全局一致
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 提示词注册表
# ---------------------------------------------------------------------------
# 每个条目定义了一个可自定义的提示词，包含：
#   id          - 唯一标识符
#   name/name_en - 中英文名称
#   description/description_en - 中英文描述
#   usage_note/usage_note_en   - 中英文使用说明
#   category     - 分类（用于前端分组）
#   file         - 对应的文件名（core/prompts/ 下的 .txt 是唯一默认内容源）
#   variables    - 模板中可用的变量列表
# ---------------------------------------------------------------------------

PROMPT_REGISTRY: dict[str, dict[str, Any]] = {
    "group_chat_prompt": {
        "id": "group_chat_prompt",
        "name": "群聊记忆总结 Prompt",
        "name_en": "Group Chat Memory Prompt",
        "description": "群聊场景下总结对话历史、提取结构化记忆的提示词模板",
        "description_en": "Template for summarizing group chat history and extracting structured memories",
        "usage_note": "⚠️ 必须要求 LLM 输出 JSON：summary/topics/key_facts/sentiment/importance/participants。格式错误将导致记忆写入失败。",
        "usage_note_en": "⚠️ MUST require the LLM to output JSON: summary/topics/key_facts/sentiment/importance/participants. Malformed output breaks memory writing.",
        "category": "memory_processing",
        "file": "group_chat_prompt.txt",
        "variables": ["{conversation}", "{current_date}"],
    },
    "private_chat_prompt": {
        "id": "private_chat_prompt",
        "name": "私聊记忆总结 Prompt",
        "name_en": "Private Chat Memory Prompt",
        "description": "私聊场景下总结对话历史、提取结构化记忆的提示词模板",
        "description_en": "Template for summarizing private chat history and extracting structured memories",
        "usage_note": "⚠️ 必须要求 LLM 输出 JSON：summary/topics/key_facts/sentiment/importance。格式错误将导致记忆写入失败。",
        "usage_note_en": "⚠️ MUST require the LLM to output JSON: summary/topics/key_facts/sentiment/importance. Malformed output breaks memory writing.",
        "category": "memory_processing",
        "file": "private_chat_prompt.txt",
        "variables": ["{conversation}", "{current_date}"],
    },
    "memory_system_prompt_base": {
        "id": "memory_system_prompt_base",
        "name": "记忆处理器基础 System Prompt",
        "name_en": "Memory System Prompt (Base)",
        "description": "记忆总结时使用的系统提示词基础模板（无人格附加内容）",
        "description_en": "Base system prompt template used for memory summarization (without persona content)",
        "usage_note": "每次记忆总结时作为 system prompt 发送给 LLM。默认保持固定内容，当前时间在对话提示词末尾提供，利于前缀缓存。自定义模板仍支持 {current_date}。",
        "usage_note_en": "Sent as the system prompt on every summary. Defaults stay static; the current time is provided at the end of the conversation prompt for prefix caching. Custom templates still support {current_date}.",
        "category": "system_prompt",
        "file": "memory_system_prompt_base.txt",
        "variables": ["{current_date}"],
    },
    "memory_system_prompt_with_persona": {
        "id": "memory_system_prompt_with_persona",
        "name": "记忆处理器人格 System Prompt",
        "name_en": "Memory System Prompt (With Persona)",
        "description": "记忆总结时使用的系统提示词完整模板，包含人格设定部分",
        "description_en": "Full system prompt template used for memory summarization, including the persona section",
        "usage_note": "仅当用户配置了 Bot 人格时使用。{base_prompt}/{persona_prompt}/{current_date} 会被对应内容替换。",
        "usage_note_en": "Used only when a bot persona is configured. {base_prompt}/{persona_prompt}/{current_date} are replaced with the corresponding content.",
        "category": "system_prompt",
        "file": "memory_system_prompt_with_persona.txt",
        "variables": ["{base_prompt}", "{persona_prompt}", "{current_date}"],
    },
    "memory_injection_header": {
        "id": "memory_injection_header",
        "name": "记忆注入头部文本",
        "name_en": "Memory Injection Header",
        "description": "向 LLM 注入历史记忆时使用的头部说明文本，告知 LLM 如何处理历史记忆",
        "description_en": "Header text used when injecting historical memories, telling the LLM how to treat them",
        "usage_note": "在每次对话中自动拼接到 LLM 上下文的最前面，告知 LLM 这些是历史记忆参考。不是 JSON 输出模板。",
        "usage_note_en": "Automatically prepended to the LLM context in every conversation, marking these as historical memory references. Not a JSON output template.",
        "category": "memory_injection",
        "file": "memory_injection_header.txt",
        "variables": [],
    },
    "memory_injection_footer": {
        "id": "memory_injection_footer",
        "name": "记忆注入尾部文本",
        "name_en": "Memory Injection Footer",
        "description": "向 LLM 注入历史记忆时使用的尾部提醒文本，提醒 LLM 关注当前对话",
        "description_en": "Footer text used when injecting historical memories, reminding the LLM to focus on the current conversation",
        "usage_note": "在记忆内容末尾自动拼接，提醒 LLM 以上是历史记录，应以当前对话为准。不是 JSON 输出模板。",
        "usage_note_en": "Automatically appended after the memory content, reminding the LLM that the above is historical and the current conversation takes precedence. Not a JSON output template.",
        "category": "memory_injection",
        "file": "memory_injection_footer.txt",
        "variables": [],
    },
    "agent_recall_policy": {
        "id": "agent_recall_policy",
        "name": "自主回忆规则（Beta）",
        "name_en": "Agentic Recall Policy (Beta)",
        "description": "Beta 自主多轮回忆注入给主对话模型的规则，说明何时查证、可用工具与停止条件",
        "description_en": "Policy injected to the main conversation model for the Beta agentic recall: when to verify, available tools, and stop conditions",
        "usage_note": "仅在主动回忆工具与 Beta 开关同时开启、且当前请求实际提供相关工具时，作为临时片段注入。{available_tools} 会被替换为当前请求实际可用的记忆工具说明。",
        "usage_note_en": "Injected as a temporary part only when both the recall tool and the Beta switch are on and the request actually offers the tools. {available_tools} is replaced with the memory tools available in this request.",
        "category": "agent_recall",
        "file": "agent_recall_policy.txt",
        "variables": ["{available_tools}"],
    },
}

# 分类信息（用于前端分组展示）
PROMPT_CATEGORIES: dict[str, dict[str, str]] = {
    "memory_processing": {
        "name": "记忆处理",
        "name_en": "Memory Processing",
        "description": "控制记忆提取与结构化总结的提示词",
        "description_en": "Prompts controlling memory extraction and structured summarization",
    },
    "system_prompt": {
        "name": "系统提示词",
        "name_en": "System Prompt",
        "description": "记忆处理器内部使用的系统提示词模板",
        "description_en": "System prompt templates used inside the memory processor",
    },
    "memory_injection": {
        "name": "记忆注入",
        "name_en": "Memory Injection",
        "description": "向 LLM 上下文注入历史记忆时的说明与提醒文本",
        "description_en": "Explanatory and reminder text injected into the LLM context with historical memories",
    },
    "agent_recall": {
        "name": "自主回忆（Beta）",
        "name_en": "Agentic Recall (Beta)",
        "description": "Beta 自主多轮回忆注入给主对话模型的规则与能力说明",
        "description_en": "Rules and capability notes injected to the main conversation model for the Beta agentic multi-round recall",
    },
}


# ---------------------------------------------------------------------------
# 单例管理
# ---------------------------------------------------------------------------

_prompt_manager: PromptManager | None = None


def init_prompt_manager(data_dir: str) -> PromptManager:
    """初始化全局 PromptManager 单例。应在插件初始化阶段调用。"""
    global _prompt_manager
    _prompt_manager = PromptManager(data_dir)
    logger.info(f"[PromptManager] 已初始化，数据目录: {data_dir}")
    return _prompt_manager


def get_prompt_manager() -> PromptManager | None:
    """获取全局 PromptManager 单例。未初始化时返回 None。"""
    return _prompt_manager


# ---------------------------------------------------------------------------
# PromptManager
# ---------------------------------------------------------------------------


class PromptManager:
    """提示词管理器。

    职责：
    - 维护提示词注册表
    - 从内置目录加载默认 prompt
    - 从数据目录读写用户自定义 prompt
    - 内存缓存加速读取
    """

    def __init__(self, data_dir: str = "") -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self._builtin_dir = Path(__file__).parent
        self._cache: dict[str, str] = {}

    # ---- 公共 API ---------------------------------------------------------

    @property
    def data_dir(self) -> Path | None:
        return self._data_dir

    @staticmethod
    def get_registry() -> dict[str, dict[str, Any]]:
        """返回提示词注册表（包含元数据，不含内容）。"""
        return PROMPT_REGISTRY

    @staticmethod
    def get_categories() -> dict[str, dict[str, str]]:
        """返回分类信息。"""
        return PROMPT_CATEGORIES

    def list_prompts(self) -> list[dict[str, Any]]:
        """列出所有提示词的元数据 + 是否已自定义。"""
        result: list[dict[str, Any]] = []
        for prompt_id, meta in PROMPT_REGISTRY.items():
            entry = dict(meta)
            entry["is_custom"] = bool(
                self._data_dir and self._custom_path(prompt_id).exists()
            )
            entry["category_name"] = PROMPT_CATEGORIES.get(
                meta.get("category", ""), {}
            ).get("name", meta.get("category", ""))
            result.append(entry)
        return result

    def get_prompt(self, prompt_id: str) -> str:
        """获取提示词内容。

        优先级：用户自定义 > 内置 .txt（唯一默认内容源）
        """
        if prompt_id not in PROMPT_REGISTRY:
            raise KeyError(f"未知的提示词 ID: {prompt_id}")

        if prompt_id in self._cache:
            return self._cache[prompt_id]

        content = self._read_prompt(prompt_id)
        self._cache[prompt_id] = content
        return content

    def get_prompt_detail(self, prompt_id: str) -> dict[str, Any]:
        """获取单个提示词的完整信息（元数据 + 内容 + 是否自定义）。"""
        meta = PROMPT_REGISTRY.get(prompt_id)
        if not meta:
            raise KeyError(f"未知的提示词 ID: {prompt_id}")
        content = self.get_prompt(prompt_id)
        return {
            **meta,
            "content": content,
            "is_custom": bool(self._data_dir and self._custom_path(prompt_id).exists()),
            "category_name": PROMPT_CATEGORIES.get(meta.get("category", ""), {}).get(
                "name", meta.get("category", "")
            ),
        }

    def update_prompt(self, prompt_id: str, content: str) -> None:
        """更新提示词（保存为自定义覆盖）。"""
        if prompt_id not in PROMPT_REGISTRY:
            raise KeyError(f"未知的提示词 ID: {prompt_id}")
        if not self._data_dir:
            raise RuntimeError("数据目录未设置，无法保存自定义提示词")

        custom_path = self._custom_path(prompt_id)
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text(content, encoding="utf-8")
        self._cache[prompt_id] = content
        logger.info(
            f"[PromptManager] 已保存自定义提示词 '{prompt_id}' -> {custom_path}"
        )

    def reset_prompt(self, prompt_id: str) -> None:
        """重置提示词为内置默认值（删除自定义覆盖文件）。"""
        if prompt_id not in PROMPT_REGISTRY:
            raise KeyError(f"未知的提示词 ID: {prompt_id}")

        self._cache.pop(prompt_id, None)

        if self._data_dir:
            custom_path = self._custom_path(prompt_id)
            if custom_path.exists():
                custom_path.unlink()
                logger.info(
                    f"[PromptManager] 已删除自定义提示词 '{prompt_id}'，恢复到内置默认"
                )

    def invalidate_cache(self, prompt_id: str | None = None) -> None:
        """清除缓存。不传参数则清除全部。"""
        if prompt_id:
            self._cache.pop(prompt_id, None)
        else:
            self._cache.clear()

    def get_default_content(self, prompt_id: str) -> str:
        """获取内置默认内容（不碰缓存和自定义文件）。"""
        if prompt_id not in PROMPT_REGISTRY:
            raise KeyError(f"未知的提示词 ID: {prompt_id}")
        builtin_path = self._builtin_dir / PROMPT_REGISTRY[prompt_id]["file"]
        return builtin_path.read_text(encoding="utf-8")

    # ---- 内部方法 ---------------------------------------------------------

    def _custom_path(self, prompt_id: str) -> Path:
        """获取自定义提示词的文件路径。"""
        meta = PROMPT_REGISTRY[prompt_id]
        return self._data_dir / "prompts" / meta["file"]  # type: ignore[union-attr]

    def _read_prompt(self, prompt_id: str) -> str:
        """读取提示词内容：用户自定义覆盖优先，否则读内置 .txt。"""
        file_name = PROMPT_REGISTRY[prompt_id]["file"]

        if self._data_dir:
            custom_path = self._custom_path(prompt_id)
            if custom_path.exists():
                logger.debug(f"[PromptManager] 加载自定义提示词 '{prompt_id}'")
                return custom_path.read_text(encoding="utf-8")

        logger.debug(f"[PromptManager] 加载内置提示词 '{prompt_id}'")
        return (self._builtin_dir / file_name).read_text(encoding="utf-8")
