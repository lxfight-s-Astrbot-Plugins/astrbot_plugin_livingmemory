"""
message_utils.py - 消息处理工具函数
提供消息截断、长度检查等功能
"""

from astrbot.api import logger

# 单条消息最大长度（约8k tokens，按4字节/token估算）
MAX_SINGLE_MESSAGE_LENGTH = 30000  # 30KB


def truncate_message_if_needed(
    content: str, max_length: int = MAX_SINGLE_MESSAGE_LENGTH
) -> tuple[str, bool]:
    """
    检测并截断过长消息

    Args:
        content: 消息内容
        max_length: 最大长度限制

    Returns:
        tuple: (处理后的内容, 是否被截断)
    """
    if len(content) > max_length:
        truncated_content = content[:max_length] + "...[内容过长已截断]"
        return truncated_content, True
    return content, False


async def store_round_with_length_check(
    memory_engine,
    user_msg,
    assistant_msg,
    session_id: str,
    persona_id: str,
    round_index: int,
) -> tuple[bool, str]:
    """
    存储一轮对话，带长度检查和截断

    Args:
        memory_engine: 记忆引擎实例
        user_msg: 用户消息对象
        assistant_msg: 助手消息对象
        session_id: 会话ID
        persona_id: 人格ID
        round_index: 轮次索引

    Returns:
        tuple: (是否成功, 错误信息)
    """
    # 检测并截断过长消息
    user_content, user_truncated = truncate_message_if_needed(user_msg.content)
    assistant_content, assistant_truncated = truncate_message_if_needed(
        assistant_msg.content
    )

    if user_truncated:
        logger.warning(
            f"[{session_id}] 用户消息过长({len(user_msg.content)}字符)，截断至{MAX_SINGLE_MESSAGE_LENGTH}"
        )

    if assistant_truncated:
        logger.warning(
            f"[{session_id}] 助手消息过长({len(assistant_msg.content)}字符)，截断至{MAX_SINGLE_MESSAGE_LENGTH}"
        )

    round_content = (
        f"{user_msg.role}: {user_content}\n{assistant_msg.role}: {assistant_content}"
    )

    # 最终检查：如果合并后仍超长，跳过此轮
    if len(round_content) > MAX_SINGLE_MESSAGE_LENGTH * 2:
        error_msg = f"第{round_index}轮对话即使截断后仍过长({len(round_content)}字符)"
        logger.error(f"[{session_id}] {error_msg}，跳过存储")
        return False, error_msg

    round_metadata = {
        "fallback": True,
        "round_storage": True,
        "round_index": round_index,
        "truncated": user_truncated or assistant_truncated,
    }

    try:
        if memory_engine is None:
            return False, "memory_engine为None"

        await memory_engine.add_memory(
            content=round_content,
            session_id=session_id,
            persona_id=persona_id,
            importance=0.5,
            metadata=round_metadata,
        )
        return True, ""
    except Exception as e:
        error_msg = f"存储失败: {str(e)}"
        logger.error(f"[{session_id}] 第{round_index}轮对话{error_msg}", exc_info=True)
        return False, error_msg
