"""
chatroom_parser.py - 群聊上下文解析器
用于从AstrBot"群聊上下文感知"模式的特殊格式中提取真实用户消息
"""

import re

from astrbot.api import logger


class ChatroomContextParser:
    """
    简单的解析器：从群聊上下文格式中提取用户的最新消息

    输入格式示例：
    "You are now in a chatroom. The chat history is as follows:
    [User A/10:30:15]: 消息1
    ---
    [User B/10:30:40]: 消息2
    ---
    Now, a new message is coming: `\n[User ID: 123456789, Nickname: User B]\n辛苦了！希望能快点恢复。`.
    Please react to it..."

    提取结果：
    "辛苦了！希望能快点恢复。"
    """

    # 检测特征
    CHATROOM_HEADER = "You are now in a chatroom. The chat history is as follows:"
    NEW_MESSAGE_MARKER = "Now, a new message is coming:"

    # 提取最新消息的正则（兼容有无User ID标识的情况）
    # 匹配格式: `[User ID: xxx, Nickname: xxx]\n实际消息` 或 `实际消息`
    NEW_MESSAGE_PATTERN = re.compile(
        r"Now, a new message is coming:\s*`\s*(?:\[User ID:[^\]]+\]\s*\n)?(.+?)`",
        re.DOTALL,
    )

    @classmethod
    def is_chatroom_context(cls, prompt: str) -> bool:
        """判断是否为群聊上下文格式"""
        return cls.CHATROOM_HEADER in prompt and cls.NEW_MESSAGE_MARKER in prompt

    @classmethod
    def extract_actual_message(cls, prompt: str) -> str:
        """
        提取真实的用户消息

        Args:
            prompt: 原始prompt（可能包含完整聊天历史）

        Returns:
            提取的用户消息，如果不是群聊格式或提取失败则返回原始prompt
        """
        if not cls.is_chatroom_context(prompt):
            return prompt

        try:
            match = cls.NEW_MESSAGE_PATTERN.search(prompt)
            if match:
                actual_message = match.group(1).strip()
                logger.debug(
                    f"[ChatroomParser] 提取到用户消息: {actual_message[:100]}..."
                )
                return actual_message
            else:
                logger.warning(
                    "[ChatroomParser] 无法从群聊格式中提取消息，返回原始prompt"
                )
                return prompt

        except (IndexError, AttributeError) as e:
            logger.error(f"[ChatroomParser] 正则匹配错误: {e}", exc_info=True)
            return prompt
        except Exception as e:
            logger.error(f"[ChatroomParser] 提取失败: {e}", exc_info=True)
            return prompt
