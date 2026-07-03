"""
prompts 包 - 提示词管理与持久化

提供 PromptManager 用于集中管理插件所有可自定义的提示词模板。
支持内置默认值和用户自定义覆盖，通过文件系统持久化。
"""

from .prompt_manager import PromptManager, get_prompt_manager, init_prompt_manager

__all__ = ["PromptManager", "get_prompt_manager", "init_prompt_manager"]
