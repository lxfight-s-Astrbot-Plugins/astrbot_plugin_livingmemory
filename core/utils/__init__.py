"""
utils 子模块
"""

import re
import time

from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..processors.text_processor import TextProcessor
from .stopwords_manager import StopwordsManager, get_stopwords_manager


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
        default_persona = await context.persona_manager.get_default_persona_v3(umo=umo)
        persona_id = default_persona["name"] if default_persona else None
        logger.debug(f"[get_persona_id] [{umo}] 使用默认人格: {persona_id or '未设置'}")
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


__all__ = [
    "StopwordsManager",
    "get_stopwords_manager",
    "TextProcessor",
    "safe_parse_metadata",
    "validate_timestamp",
    "OperationContext",
    "get_persona_id",
    "extract_json_from_response",
    "format_memories_for_injection",
    "format_memories_for_fake_tool_call",
    "format_memories_for_fake_tool_call_deepseek_v4",
]


# ---- 记忆注入默认文本（后备） ----

# 自 formatting.py 拆分模块导出（保持向后兼容）
from .formatting import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
    safe_parse_metadata,
    validate_timestamp,
)
