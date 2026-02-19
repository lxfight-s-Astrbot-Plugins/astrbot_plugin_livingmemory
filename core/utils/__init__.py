"""
utils 子模块
"""

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

import pytz

from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..processors.text_processor import TextProcessor
from .stopwords_manager import StopwordsManager, get_stopwords_manager
from .time_resolver import resolve_relative_time


def safe_parse_metadata(metadata_raw: Any) -> dict[str, Any]:
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


def safe_serialize_metadata(metadata: dict[str, Any]) -> str:
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


def validate_timestamp(timestamp: Any, default_time: float | None = None) -> float:
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
    elif hasattr(timestamp, "timestamp"):  # datetime对象
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
    **kwargs,
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
    last_exception: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_factor * (2**attempt)
                logger.warning(
                    f"函数 {func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}"
                )
                logger.info(f"等待 {wait_time:.2f} 秒后重试...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"函数 {func.__name__} 重试 {max_retries} 次后仍然失败: {e}"
                )

    # 所有重试都失败，抛出最后一个异常
    if last_exception is not None:
        raise last_exception


class OperationContext:
    """操作上下文管理器，用于错误处理和资源清理"""

    def __init__(self, operation_name: str, session_id: str | None = None):
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
            logger.debug(
                f"{session_info}操作成功完成: {self.operation_name} (耗时 {duration:.3f}s)"
            )
        else:
            logger.error(
                f"{session_info}操作失败: {self.operation_name} (耗时 {duration:.3f}s) - {exc_val}"
            )

        # 不抑制异常，让调用者处理
        return False


async def get_persona_id(context: Context, event: AstrMessageEvent) -> str | None:
    """
    获取当前会话的人格 ID，与 AstrBot 主流程保持完全一致的三级优先级：
      1. session_service_config（最高，由 /persona 等命令写入）
      2. conversation.persona_id（会话级绑定）
      3. 全局默认人格（最低）
    """
    try:
        umo = event.unified_msg_origin

        # 优先级 1：session_service_config（与 _ensure_persona_and_skills 一致）
        session_persona_id: str | None = (
            await sp.get_async(
                scope="umo",
                scope_id=umo,
                key="session_service_config",
                default={},
            )
        ).get("persona_id")

        if session_persona_id:
            logger.debug(
                f"[get_persona_id] [{umo}] 使用 session_service_config 人格: {session_persona_id}"
            )
            return session_persona_id

        # 优先级 2：conversation.persona_id
        session_id = await context.conversation_manager.get_curr_conversation_id(umo)
        if session_id is None:
            logger.debug(f"[get_persona_id] [{umo}] 无当前会话，跳至默认人格")
        else:
            conversation = await context.conversation_manager.get_conversation(
                umo, session_id
            )
            persona_id = conversation.persona_id if conversation else None

            logger.debug(
                f"[get_persona_id] [{umo}] 会话={session_id}, "
                f"会话人格={persona_id or '未设置'}"
            )

            if persona_id == "[%None]":
                # 明确设置为无人格
                logger.debug(f"[get_persona_id] [{umo}] 会话明确设置为无人格")
                return None

            if persona_id:
                logger.info(f"[get_persona_id] [{umo}] 最终使用人格: {persona_id}")
                return persona_id

        # 优先级 3：全局默认人格
        default_persona = await context.persona_manager.get_default_persona_v3(
            umo=umo
        )
        persona_id = default_persona["name"] if default_persona else None
        logger.debug(
            f"[get_persona_id] [{umo}] 使用默认人格: {persona_id or '未设置'}"
        )
        logger.info(f"[get_persona_id] [{umo}] 最终使用人格: {persona_id or '无'}")
        return persona_id
    except Exception as e:
        logger.debug(f"获取人格ID失败: {e}")
        return None


def extract_json_from_response(text: str) -> str:
    """
    从可能包含 Markdown 代码块的文本中提取纯 JSON 字符串。
    """
    # 查找被 ```json ... ``` 或 ``` ... ``` 包围的内容
    match = re.search(r"```(json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        # 返回捕获组中的 JSON 部分
        return match.group(2)

    # 如果没有找到代码块，假设整个文本就是 JSON（可能需要去除首尾空格）
    return text.strip()


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
        if hasattr(context, "plugin_config"):
            config = getattr(context, "plugin_config", {})
            if isinstance(config, dict):
                tz_str = config.get("timezone_settings", {}).get(
                    "timezone", "Asia/Shanghai"
                )
                return get_now_datetime(tz_str)
        # 如果配置不存在，则使用默认值
        return get_now_datetime()
    except (AttributeError, KeyError):
        # 如果配置不存在，则使用默认值
        return get_now_datetime()


def format_memories_for_injection(memories: list) -> str:
    """
    将检索到的记忆列表格式化为单个字符串，以便注入到 System Prompt。
    添加明确的说明文本，告知 LLM 这些是历史对话记忆。
    """
    # 延迟导入避免循环依赖
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    if not memories:
        return ""

    # 添加更详细的说明文本，包含当前搜索时间
    current_time_str = get_now_datetime().strftime("%Y-%m-%d %H:%M")
    header = (
        f"{MEMORY_INJECTION_HEADER}\n"
        f"当前时间: {current_time_str}\n"
        f"以下是从历史对话中提取的相关记忆，可以帮助你更好地理解用户的背景、偏好和过往交流内容。\n"
        f"请参考这些记忆来提供更个性化、更连贯的回答。\n\n"
    )
    footer = (
        f"\n\n"
        f"注意：以上记忆来自历史对话，请结合当前对话上下文使用这些信息。\n"
        f"{MEMORY_INJECTION_FOOTER}"
    )

    logger.debug(
        f"[format_memories_for_injection] 记忆注入标记: 头部='{MEMORY_INJECTION_HEADER}', 尾部='{MEMORY_INJECTION_FOOTER}'"
    )

    formatted_entries = []
    for idx, mem in enumerate(memories, 1):
        try:
            # 修复：memories 传入的是字典列表，不是对象
            # 从字典中获取数据
            if isinstance(mem, dict):
                content = mem.get("content", "内容缺失")
                score = mem.get("score", 0.0)
                metadata = mem.get("metadata", {})
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "未知")
            else:
                # 如果是对象，尝试访问属性
                content = getattr(mem, "content", "内容缺失")
                score = getattr(mem, "score", 0.0)
                metadata_raw = getattr(mem, "metadata", {})
                metadata = (
                    safe_parse_metadata(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else metadata_raw
                )
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "未知")

            # 格式化时间戳
            time_str = ""
            timestamp = metadata.get("create_time")
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(validate_timestamp(timestamp))
                    time_str = f"{dt.strftime('%Y-%m-%d %H:%M')}"
                except Exception:
                    pass

            # 构建格式化的记忆条目（展示content和元数据信息）
            entry_parts = [
                f"记忆 #{idx} (重要性: {importance:.2f}), 发生时间:{time_str}"
            ]

            # 添加元数据信息
            metadata_parts = []

            # 添加主题
            topics = metadata.get("topics", [])
            if topics and isinstance(topics, list) and len(topics) > 0:
                topics_str = "、".join(str(t) for t in topics if t)
                if topics_str:
                    metadata_parts.append(f"主题: {topics_str}")

            # 添加参与者（仅群聊）
            participants = metadata.get("participants", [])
            if (
                participants
                and isinstance(participants, list)
                and len(participants) > 0
            ):
                participants_str = "、".join(str(p) for p in participants if p)
                if participants_str:
                    metadata_parts.append(f"参与者: {participants_str}")

            # 添加关键事实
            key_facts = metadata.get("key_facts", [])
            if key_facts and isinstance(key_facts, list) and len(key_facts) > 0:
                facts_str = "; ".join(str(f) for f in key_facts if f)
                if facts_str:
                    metadata_parts.append(f"关键信息: {facts_str}")

            # 组装元数据行
            if metadata_parts:
                entry_parts.append(" | ".join(metadata_parts))

            # 添加记忆内容
            entry_parts.append(content)

            entry = "\n".join(entry_parts)
            formatted_entries.append(entry)

            logger.debug(
                f"[format_memories_for_injection] 格式化记忆 #{idx}: 重要性={importance:.2f}, "
                f"得分={score:.2f}, 类型={interaction_type}, 内容长度={len(content)}"
            )
        except Exception as e:
            # 如果处理失败，则跳过此条记忆
            logger.warning(
                f"[format_memories_for_injection] 格式化记忆时出错，跳过此记忆: {e}, "
                f"记忆对象类型: {type(mem)}"
            )
            continue

    if not formatted_entries:
        logger.debug("[format_memories_for_injection] 没有记忆需要格式化，返回空字符串")
        return ""

    body = "\n\n".join(formatted_entries)
    result = f"{header}{body}{footer}"

    logger.info(
        f"[format_memories_for_injection]  记忆格式化完成: 记忆条数={len(formatted_entries)}, "
        f"总长度={len(result)}"
    )
    logger.debug(
        f"[format_memories_for_injection] 包含标记验证: "
        f"头部={MEMORY_INJECTION_HEADER in result}, 尾部={MEMORY_INJECTION_FOOTER in result}"
    )

    return result


__all__ = [
    "StopwordsManager",
    "get_stopwords_manager",
    "TextProcessor",
    "safe_parse_metadata",
    "safe_serialize_metadata",
    "validate_timestamp",
    "retry_on_failure",
    "OperationContext",
    "get_persona_id",
    "extract_json_from_response",
    "get_now_datetime",
    "get_now_datetime_from_context",
    "format_memories_for_injection",
    "resolve_relative_time",
]
