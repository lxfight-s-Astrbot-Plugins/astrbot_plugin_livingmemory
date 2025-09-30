# -*- coding: utf-8 -*-
"""
utils.py - 插件的辅助工具函数
"""

import re
import json
import time
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any

import pytz

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from ..storage.faiss_manager import Result
from .constants import MEMORY_INJECTION_HEADER, MEMORY_INJECTION_FOOTER


def safe_parse_metadata(metadata_raw: Any) -> Dict[str, Any]:
    """
    安全解析元数据，统一处理字符串和字典类型。
    
    Args:
        metadata_raw: 原始元数据，可能是字符串或字典
        
    Returns:
        Dict[str, Any]: 解析后的元数据字典，解析失败时返回空字典
    """
    if isinstance(metadata_raw, dict):
        return metadata_raw
    elif isinstance(metadata_raw, str):
        try:
            return json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"解析元数据JSON失败: {e}, 原始数据: {metadata_raw}")
            return {}
    else:
        logger.warning(f"不支持的元数据类型: {type(metadata_raw)}")
        return {}


def safe_serialize_metadata(metadata: Dict[str, Any]) -> str:
    """
    安全序列化元数据为JSON字符串。
    
    Args:
        metadata: 元数据字典
        
    Returns:
        str: JSON字符串
    """
    try:
        return json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.error(f"序列化元数据失败: {e}, 数据: {metadata}")
        return "{}"


def validate_timestamp(timestamp: Any, default_time: Optional[float] = None) -> float:
    """
    验证和标准化时间戳。
    
    Args:
        timestamp: 时间戳，可能是字符串、数字或其他类型
        default_time: 默认时间，如果为None则使用当前时间
        
    Returns:
        float: 标准化的时间戳
    """
    if default_time is None:
        default_time = time.time()
        
    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    elif isinstance(timestamp, str):
        try:
            return float(timestamp)
        except (ValueError, TypeError):
            logger.warning(f"无法解析时间戳字符串: {timestamp}")
            return default_time
    elif hasattr(timestamp, 'timestamp'):  # datetime对象
        try:
            return timestamp.timestamp()
        except Exception as e:
            logger.warning(f"无法从datetime对象获取时间戳: {e}")
            return default_time
    else:
        logger.warning(f"不支持的时间戳类型: {type(timestamp)}")
        return default_time


async def retry_on_failure(
    func, 
    *args, 
    max_retries: int = 3, 
    backoff_factor: float = 1.0,
    exceptions: tuple = (Exception,),
    **kwargs
):
    """
    带重试机制的函数执行器。
    
    Args:
        func: 要执行的函数
        *args: 函数位置参数
        max_retries: 最大重试次数
        backoff_factor: 退避因子
        exceptions: 需要重试的异常类型
        **kwargs: 函数关键字参数
        
    Returns:
        函数执行结果
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_factor * (2 ** attempt)
                logger.warning(f"函数 {func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}")
                logger.info(f"等待 {wait_time:.2f} 秒后重试...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"函数 {func.__name__} 重试 {max_retries} 次后仍然失败: {e}")
                
    # 所有重试都失败，抛出最后一个异常
    raise last_exception


class OperationContext:
    """操作上下文管理器，用于错误处理和资源清理"""
    
    def __init__(self, operation_name: str, session_id: Optional[str] = None):
        self.operation_name = operation_name
        self.session_id = session_id
        self.start_time = None
        
    async def __aenter__(self):
        self.start_time = time.time()
        session_info = f"[{self.session_id}] " if self.session_id else ""
        logger.debug(f"{session_info}开始执行操作: {self.operation_name}")
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time if self.start_time else 0
        session_info = f"[{self.session_id}] " if self.session_id else ""
        
        if exc_type is None:
            logger.debug(f"{session_info}操作成功完成: {self.operation_name} (耗时 {duration:.3f}s)")
        else:
            logger.error(f"{session_info}操作失败: {self.operation_name} (耗时 {duration:.3f}s) - {exc_val}")
            
        # 不抑制异常，让调用者处理
        return False


async def get_persona_id(context: Context, event: AstrMessageEvent) -> Optional[str]:
    """
    获取当前会话的人格 ID。
    如果当前会话没有特定人格，则返回 AstrBot 的默认人格。
    """
    try:
        session_id = await context.conversation_manager.get_curr_conversation_id(
            event.unified_msg_origin
        )
        conversation = await context.conversation_manager.get_conversation(
            event.unified_msg_origin, session_id
        )
        persona_id = conversation.persona_id if conversation else None

        # 如果无人格或明确设置为None，则使用全局默认人格
        if not persona_id or persona_id == "[%None]":
            default_persona = context.provider_manager.selected_default_persona
            persona_id = default_persona["name"] if default_persona else None

        return persona_id
    except Exception as e:
        # 在某些情况下（如无会话），获取可能会失败，返回 None
        logger.debug(f"获取人格ID失败: {e}")
        return None


def extract_json_from_response(text: str) -> str:
    """
    从可能包含 Markdown 代码块的文本中提取纯 JSON 字符串。
    支持对象 {...} 和数组 [{...}, {...}] 格式。
    """
    # 尝试匹配Markdown代码块中的JSON(支持对象和数组)
    # 模式1: ```json ... ```
    # 模式2: ``` ... ```
    match = re.search(r"```(?:json)?\s*([{\[].*?[}\]])\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    # 如果没有找到代码块,尝试直接提取JSON
    # 查找第一个完整的JSON对象或数组
    # 对象: { ... }
    # 数组: [ ... ]
    json_start = -1
    bracket_type = None

    for i, char in enumerate(text):
        if char == '{':
            json_start = i
            bracket_type = '{'
            break
        elif char == '[':
            json_start = i
            bracket_type = '['
            break

    if json_start == -1:
        # 没有找到JSON起始符,返回原文本
        return text.strip()

    # 匹配括号,找到JSON结束位置
    stack = []
    closing_bracket = '}' if bracket_type == '{' else ']'

    for i in range(json_start, len(text)):
        char = text[i]
        if char == bracket_type:
            stack.append(char)
        elif char == closing_bracket:
            if stack:
                stack.pop()
                if not stack:
                    # 找到完整的JSON
                    return text[json_start:i+1]

    # 如果括号不匹配,返回从起始位置到文本结尾
    return text[json_start:].strip()


def get_now_datetime(tz_str: str = "Asia/Shanghai") -> datetime:
    """
    获取当前时间，并根据指定的时区设置时区。
    
    Args:
        tz_str: 时区字符串，默认为 "Asia/Shanghai"
        
    Returns:
        datetime: 带有时区信息的当前时间
    """
    # 如果传入的是 Context 对象，则使用从上下文获取时间的方法
    # 检查传入的是否是 Context 对象
    from astrbot.api.star import Context  # 导入 Context 类型
    if isinstance(tz_str, Context):
        # 如果是 Context 对象，调用专门的函数处理
        return get_now_datetime_from_context(tz_str)
        
    try:
        timezone = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        # 如果时区无效，则使用默认值
        logger.warning(f"无效的时区: {tz_str}，使用默认时区 Asia/Shanghai")
        timezone = pytz.timezone("Asia/Shanghai")

    return datetime.now(timezone)


def get_now_datetime_from_context(context: Context) -> datetime:
    """
    从上下文中获取当前时间，根据插件配置设置时区。
    
    Args:
        context: AstrBot 上下文对象
        
    Returns:
        datetime: 带有时区信息的当前时间
    """
    try:
        # 尝试从配置中获取时区
        tz_str = context.plugin_config.get("timezone_settings", {}).get("timezone", "Asia/Shanghai")
        return get_now_datetime(tz_str)
    except (AttributeError, KeyError):
        # 如果配置不存在，则使用默认值
        return get_now_datetime()


def format_memories_for_injection(memories: List[Result]) -> str:
    """
    将检索到的记忆列表格式化为单个字符串，以便注入到 System Prompt。
    """
    if not memories:
        return ""

    header = f"{MEMORY_INJECTION_HEADER}\n"
    footer = f"\n{MEMORY_INJECTION_FOOTER}"

    formatted_entries = []
    for mem in memories:
        try:
            # 使用统一的元数据解析函数
            metadata_raw = mem.data.get("metadata", "{}")
            metadata = safe_parse_metadata(metadata_raw)

            content = mem.data.get("text", "内容缺失")
            importance = metadata.get("importance", 0.0)

            entry = f"- [重要性: {importance:.2f}] {content}"
            formatted_entries.append(entry)
        except Exception as e:
            # 如果处理失败，则跳过此条记忆
            logger.debug(f"格式化记忆时出错，跳过此记忆: {e}")
            continue

    if not formatted_entries:
        return ""

    body = "\n".join(formatted_entries)

    return f"{header}{body}{footer}"
