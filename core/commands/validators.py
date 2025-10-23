# -*- coding: utf-8 -*-
"""
命令参数验证器 - 提供参数验证的通用方法
"""

from typing import Any, Dict, Optional, List, Callable
from functools import wraps

from astrbot.api import logger


class CommandValidator:
    """命令参数验证器"""

    # 常量定义
    MAX_CONTENT_LENGTH = 10000  # 记忆内容最大长度 (10KB)
    MAX_SEARCH_RESULTS = 50     # 搜索结果数量上限
    MAX_QUERY_LENGTH = 500      # 查询字符串最大长度

    @staticmethod
    def validate_memory_id(memory_id: Any) -> tuple[bool, Optional[str]]:
        """
        验证记忆 ID

        参数:
            memory_id: 要验证的记忆 ID

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(memory_id, (int, str)):
            return False, "记忆 ID 必须是整数或字符串"

        if isinstance(memory_id, str):
            if not memory_id.strip():
                return False, "记忆 ID 不能为空"
            try:
                int(memory_id)
            except ValueError:
                return False, "记忆 ID 必须是有效的整数"

        return True, None

    @staticmethod
    def validate_content_length(content: str) -> tuple[bool, Optional[str]]:
        """
        验证内容长度

        参数:
            content: 要验证的内容

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(content, str):
            return False, "内容必须是字符串"

        if len(content) > CommandValidator.MAX_CONTENT_LENGTH:
            return False, f"内容长度不能超过 {CommandValidator.MAX_CONTENT_LENGTH} 字符"

        return True, None

    @staticmethod
    def validate_search_count(k: int) -> tuple[bool, Optional[str]]:
        """
        验证搜索结果数量

        参数:
            k: 搜索结果数量

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(k, int):
            return False, "搜索数量必须是整数"

        if k < 1:
            return False, "搜索数量必须大于 0"

        if k > CommandValidator.MAX_SEARCH_RESULTS:
            return False, f"搜索数量不能超过 {CommandValidator.MAX_SEARCH_RESULTS}"

        return True, None

    @staticmethod
    def validate_query_length(query: str) -> tuple[bool, Optional[str]]:
        """
        验证查询字符串长度

        参数:
            query: 查询字符串

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(query, str):
            return False, "查询必须是字符串"

        if not query.strip():
            return False, "查询不能为空"

        if len(query) > CommandValidator.MAX_QUERY_LENGTH:
            return False, f"查询长度不能超过 {CommandValidator.MAX_QUERY_LENGTH} 字符"

        return True, None

    @staticmethod
    def validate_importance(importance: float) -> tuple[bool, Optional[str]]:
        """
        验证重要性分数

        参数:
            importance: 重要性分数

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(importance, (int, float)):
            return False, "重要性必须是数字"

        if not 0.0 <= importance <= 1.0:
            return False, "重要性必须在 0.0 到 1.0 之间"

        return True, None

    @staticmethod
    def validate_field_name(field: str, allowed_fields: List[str]) -> tuple[bool, Optional[str]]:
        """
        验证字段名称

        参数:
            field: 字段名称
            allowed_fields: 允许的字段列表

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(field, str):
            return False, "字段名必须是字符串"

        if field not in allowed_fields:
            return False, f"无效的字段名，允许的字段: {', '.join(allowed_fields)}"

        return True, None

    @staticmethod
    def validate_fusion_strategy(strategy: str, allowed_strategies: List[str]) -> tuple[bool, Optional[str]]:
        """
        验证融合策略

        参数:
            strategy: 融合策略名称
            allowed_strategies: 允许的策略列表

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(strategy, str):
            return False, "策略名称必须是字符串"

        if strategy not in allowed_strategies:
            return False, f"无效的策略，允许的策略: {', '.join(allowed_strategies)}"

        return True, None

    @staticmethod
    def validate_search_mode(mode: str) -> tuple[bool, Optional[str]]:
        """
        验证检索模式

        参数:
            mode: 检索模式

        返回:
            (是否有效, 错误消息)
        """
        valid_modes = ["hybrid", "dense", "sparse"]
        if mode not in valid_modes:
            return False, f"无效的模式，请使用: {', '.join(valid_modes)}"

        return True, None

    @staticmethod
    def validate_fusion_weight(weight: float) -> tuple[bool, Optional[str]]:
        """
        验证融合权重

        参数:
            weight: 融合权重

        返回:
            (是否有效, 错误消息)
        """
        if not isinstance(weight, (int, float)):
            return False, "权重必须是数字"

        if not 0.0 <= weight <= 1.0:
            return False, "权重必须在 0.0 到 1.0 之间"

        return True, None


def validate_params(**validators: Callable[[Any], tuple[bool, Optional[str]]]):
    """
    装饰器：验证命令参数

    用法:
        @validate_params(
            k=CommandValidator.validate_search_count,
            query=CommandValidator.validate_query_length
        )
        async def lmem_search(self, event: AstrMessageEvent, query: str, k: int = 3):
            # 参数已验证，可以安全使用
            result = await self.search_handler.search_memories(query, k)
            yield event.plain_result(...)

    参数:
        **validators: 参数名 -> 验证函数的映射
                     验证函数签名: (value) -> (is_valid, error_message)

    功能:
        1. 在命令执行前验证指定的参数
        2. 如果验证失败，返回错误消息
        3. 如果验证成功，继续执行命令
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, event, *args, **kwargs):
            # 获取函数参数名
            import inspect
            sig = inspect.signature(func)
            bound_args = sig.bind(self, event, *args, **kwargs)
            bound_args.apply_defaults()

            # 验证每个指定的参数
            for param_name, validator in validators.items():
                if param_name in bound_args.arguments:
                    value = bound_args.arguments[param_name]

                    # 执行验证
                    is_valid, error_msg = validator(value)
                    if not is_valid:
                        yield event.plain_result(f"❌ 参数验证失败: {error_msg}")
                        return

            # 所有验证通过，执行原函数
            async for result in func(self, event, *args, **kwargs):
                yield result

        return wrapper
    return decorator
