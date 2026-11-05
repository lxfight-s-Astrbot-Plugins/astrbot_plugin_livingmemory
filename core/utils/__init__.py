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
    获取当前会话的人格 ID，确保每个人格拥有严格隔离的专属记忆：
      0. 优先从 event extra 中获取 AstrBot 主流程设定的当前执行人格（_trace_persona_id）
      1. session_service_config（由 /persona 等命令写入）
      2. conversation.persona_id（会话级绑定）
      3. AstrBot agent_runner 实际配置的人格 (local runner persona_id)
      4. 全局默认人格 (default_persona_v3)
    """
    try:
        # 优先级 0：最准确的执行期追踪人格
        if hasattr(event, "get_extra"):
            trace_persona = event.get_extra("_trace_persona_id")
            if trace_persona and trace_persona != "[%None]":
                logger.info(f"[get_persona_id] 从 event trace 识别当前人格: {trace_persona}")
                return str(trace_persona)

        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            return None

        # 优先级 1：session_service_config
        session_service_conf = await sp.get_async(
            scope="umo",
            scope_id=umo,
            key="session_service_config",
            default={},
        )
        session_persona_id: str | None = session_service_conf.get("persona_id") if isinstance(session_service_conf, dict) else None

        if session_persona_id and session_persona_id != "[%None]":
            logger.info(
                f"[get_persona_id] [{umo}] 使用 session_service_config 人格: {session_persona_id}"
            )
            return str(session_persona_id)

        # 优先级 2：conversation.persona_id
        if hasattr(context, "conversation_manager"):
            session_id = await context.conversation_manager.get_curr_conversation_id(umo)
            if session_id:
                conversation = await context.conversation_manager.get_conversation(
                    umo, session_id
                )
                persona_id = conversation.persona_id if conversation else None
                if persona_id == "[%None]":
                    logger.debug(f"[get_persona_id] [{umo}] 会话明确设置为无人格")
                    return None
                if persona_id:
                    logger.info(f"[get_persona_id] [{umo}] 使用会话人格: {persona_id}")
                    return str(persona_id)

        # 优先级 3：AstrBot agent_runner 实际配置人格
        cfg = context.get_config(umo=umo) if hasattr(context, "get_config") else {}
        agent_runner = cfg.get("agent_runner", {}) if isinstance(cfg, dict) else {}
        runner_cfg = agent_runner.get("config", {}) if isinstance(agent_runner, dict) else {}
        runner_persona = (
            runner_cfg.get("persona", {}).get("persona_id")
            if agent_runner.get("runner_type") == "local"
            else runner_cfg.get("persona_id")
        )
        if runner_persona and runner_persona != "[%None]":
            logger.info(f"[get_persona_id] [{umo}] 使用 agent_runner 配置人格: {runner_persona}")
            return str(runner_persona)

        # 优先级 4：全局默认人格
        if hasattr(context, "persona_manager"):
            default_persona = await context.persona_manager.get_default_persona_v3(umo=umo)
            persona_id = default_persona["name"] if default_persona else None
            if persona_id:
                logger.info(f"[get_persona_id] [{umo}] 使用全局默认人格: {persona_id}")
                return str(persona_id)

        return "default"
    except Exception as e:
        logger.warning(f"[get_persona_id] 获取人格ID异常: {e}")
        return "default"


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
]


# ---- 记忆注入默认文本（后备） ----

# 自 formatting.py 拆分模块导出（保持向后兼容）
from .formatting import (
    format_memories_for_injection,
    safe_parse_metadata,
    validate_timestamp,
)
