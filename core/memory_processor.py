# -*- coding: utf-8 -*-
"""
记忆处理器 - 使用LLM将对话历史处理为结构化记忆
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any
from astrbot.api import logger
from .conversation_models import Message


class MemoryProcessor:
    """
    记忆处理器

    使用LLM将对话历史转换为结构化记忆。
    支持私聊和群聊两种场景的不同处理策略。
    """

    def __init__(self, llm_provider):
        """
        初始化记忆处理器

        Args:
            llm_provider: LLM提供者实例(Provider类型)
        """
        self.llm_provider = llm_provider

        # 加载提示词模板
        self._load_prompts()

    def _load_prompts(self) -> None:
        """从外部文件加载提示词模板"""
        prompt_dir = Path(__file__).parent / "prompts"

        try:
            # 加载私聊提示词
            private_prompt_file = prompt_dir / "private_chat_prompt.txt"
            with open(private_prompt_file, "r", encoding="utf-8") as f:
                self.private_chat_prompt = f.read()

            # 加载群聊提示词
            group_prompt_file = prompt_dir / "group_chat_prompt.txt"
            with open(group_prompt_file, "r", encoding="utf-8") as f:
                self.group_chat_prompt = f.read()

            logger.info("[MemoryProcessor] 提示词模板加载成功")

        except Exception as e:
            logger.error(f"[MemoryProcessor] 加载提示词模板失败: {e}")
            # 使用简单的后备提示词
            self.private_chat_prompt = """分析以下对话并生成JSON格式的记忆:
{conversation}

输出格式:
{{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], "sentiment": "neutral", "importance": 0.5}}
"""
            self.group_chat_prompt = """分析以下群聊对话并生成JSON格式的记忆:
{conversation}

输出格式:
{{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], "participants": ["参与者"], "sentiment": "neutral", "importance": 0.5}}
"""

    async def process_conversation(
        self,
        messages: List[Message],
        is_group_chat: bool = False,
        save_original: bool = False,
    ) -> tuple[str, Dict[str, Any], float]:
        """
        处理对话历史,生成结构化记忆

        Args:
            messages: 消息列表(Message对象)
            is_group_chat: 是否为群聊
            save_original: 是否保存原始对话历史（默认False，只保存LLM生成的总结）

        Returns:
            tuple: (content, metadata, importance)
                - content: 格式化的记忆内容字符串
                - metadata: 包含结构化信息的字典
                - importance: 重要性评分(0-1)

        Raises:
            Exception: 处理失败时抛出异常
        """
        if not messages:
            raise ValueError("消息列表不能为空")

        # 1. 格式化对话历史
        conversation_text = self._format_conversation(messages)

        # 2. 选择合适的提示词模板
        if is_group_chat:
            prompt = self.group_chat_prompt.format(conversation=conversation_text)
        else:
            prompt = self.private_chat_prompt.format(conversation=conversation_text)

        # 3. 调用LLM生成结构化记忆
        conversation_type = "群聊" if is_group_chat else "私聊"
        try:
            logger.info(
                f"[MemoryProcessor] 准备调用 LLM，对话类型={conversation_type}, 消息数={len(messages)}"
            )
            logger.debug(f"[MemoryProcessor] Prompt 模板长度={len(prompt)}")
            logger.debug(
                f"[MemoryProcessor] 发送给LLM的对话内容（前500字符）:\n{conversation_text[:500]}"
            )

            system_prompt = "你是一个专业的对话分析助手,擅长提取对话中的关键信息。"
            logger.debug(f"[MemoryProcessor] System Prompt: {system_prompt}")

            llm_response = await self.llm_provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt,
            )

            logger.info(
                f"[MemoryProcessor]  LLM 响应成功，响应长度={len(llm_response.completion_text)}"
            )
            logger.debug(
                f"[MemoryProcessor] LLM 原始响应内容:\n{llm_response.completion_text}"
            )

            # 4. 解析LLM响应
            structured_data = self._parse_llm_response(
                llm_response.completion_text, is_group_chat
            )

            # 5. 构建存储格式
            content, metadata = self._build_storage_format(
                conversation_text, structured_data, is_group_chat
            )

            importance = float(structured_data.get("importance", 0.5))

            logger.info(
                f"[MemoryProcessor]  成功生成结构化记忆: 摘要={structured_data.get('summary', '')[:50]}..., "
                f"主题={structured_data.get('topics', [])}, "
                f"重要性={importance}, 类型={conversation_type}"
            )
            logger.debug(
                f"[MemoryProcessor] 生成的记忆内容（前200字符）:\n{content[:200]}"
            )

            return content, metadata, importance

        except Exception as e:
            logger.error(f"[MemoryProcessor] 处理对话历史失败: {e}", exc_info=True)
            # 降级处理:使用简单的文本拼接
            return self._create_fallback_memory(conversation_text, is_group_chat)

    def _format_conversation(self, messages: List[Message]) -> str:
        """
        格式化对话历史为文本

        Args:
            messages: 消息列表(Message对象)

        Returns:
            格式化后的对话文本
        """
        formatted_lines = []
        for msg in messages:
            # 使用Message对象的format_for_llm方法
            formatted = msg.format_for_llm(include_sender_name=bool(msg.group_id))
            formatted_lines.append(f"{formatted['role']}: {formatted['content']}")
        return "\n".join(formatted_lines)

    def _parse_llm_response(
        self, response_text: str, is_group_chat: bool
    ) -> Dict[str, Any]:
        """
        解析LLM响应,提取JSON数据

        Args:
            response_text: LLM响应文本
            is_group_chat: 是否为群聊

        Returns:
            解析后的字典数据
        """
        logger.debug(f"[MemoryProcessor] 开始解析 LLM 响应，长度={len(response_text)}")

        try:
            # 尝试直接解析JSON
            # 先清理可能的markdown代码块标记
            cleaned_text = response_text.strip()
            logger.debug(
                f"[MemoryProcessor] 清理前的响应文本（前100字符）: {response_text[:100]}"
            )

            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
                logger.debug("[MemoryProcessor] 移除了 ```json 标记")
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
                logger.debug("[MemoryProcessor] 移除了 ``` 标记")
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
                logger.debug("[MemoryProcessor] 移除了结尾 ``` 标记")
            cleaned_text = cleaned_text.strip()

            logger.debug(
                f"[MemoryProcessor] 清理后准备解析的 JSON（前500字符）:\n{cleaned_text[:500]}"
            )

            # 解析JSON
            data = json.loads(cleaned_text)
            logger.info("[MemoryProcessor] JSON 解析成功")
            logger.debug(f"[MemoryProcessor] 解析得到的字段: {list(data.keys())}")

            # 验证必需字段 - 简化后的字段列表
            required_fields = [
                "summary",
                "topics",
                "key_facts",
                "sentiment",
                "importance",
            ]
            if is_group_chat:
                required_fields.append("participants")

            for field in required_fields:
                if field not in data:
                    logger.warning(
                        f"[MemoryProcessor] LLM 响应缺少字段: {field}, 使用默认值"
                    )
                    data[field] = self._get_default_value(field)

            # 数据类型校验和规范化
            data["summary"] = str(data.get("summary", ""))
            logger.debug(f"[MemoryProcessor] 提取 summary: {data['summary'][:100]}...")

            data["topics"] = self._ensure_list(data.get("topics", []))[:5]
            logger.debug(
                f"[MemoryProcessor] 提取 topics ({len(data['topics'])} 个): {data['topics']}"
            )

            data["key_facts"] = self._ensure_list(data.get("key_facts", []))[:5]
            logger.debug(
                f"[MemoryProcessor] 提取 key_facts ({len(data['key_facts'])} 个): {data['key_facts']}"
            )

            data["sentiment"] = self._validate_sentiment(
                data.get("sentiment", "neutral")
            )
            logger.debug(f"[MemoryProcessor] 提取 sentiment: {data['sentiment']}")

            data["importance"] = self._validate_importance(data.get("importance", 0.5))
            logger.debug(f"[MemoryProcessor] 提取 importance: {data['importance']}")

            if is_group_chat:
                data["participants"] = self._ensure_list(data.get("participants", []))
                logger.debug(
                    f"[MemoryProcessor] 提取 participants ({len(data['participants'])} 个): {data['participants']}"
                )

            return data

        except json.JSONDecodeError as e:
            logger.warning(f"[MemoryProcessor]  JSON 解析失败: {e}")
            logger.debug(
                f"[MemoryProcessor] 解析失败的内容（前200字符）: {response_text[:200]}"
            )
            logger.info("[MemoryProcessor] 尝试使用正则表达式提取 JSON")
            # 尝试正则提取
            return self._extract_by_regex(response_text, is_group_chat)
        except Exception as e:
            logger.error(
                f"[MemoryProcessor]  解析 LLM 响应时发生异常: {e}", exc_info=True
            )
            logger.debug(
                f"[MemoryProcessor] 异常发生时的响应内容: {response_text[:200]}"
            )
            return self._get_default_structured_data(is_group_chat)

    def _extract_by_regex(self, text: str, is_group_chat: bool) -> Dict[str, Any]:
        """
        使用正则表达式从文本中提取结构化数据(备用方案)

        Args:
            text: 响应文本
            is_group_chat: 是否为群聊

        Returns:
            提取的结构化数据
        """
        logger.debug("[MemoryProcessor] 开始使用正则表达式提取结构化数据")
        data = self._get_default_structured_data(is_group_chat)

        try:
            # 先尝试找到完整的 JSON 块
            json_matches = re.findall(
                r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL
            )
            logger.debug(
                f"[MemoryProcessor] 正则匹配到 {len(json_matches)} 个可能的 JSON 块"
            )

            for i, match in enumerate(json_matches):
                logger.debug(
                    f"[MemoryProcessor] JSON 块 #{i + 1} (前200字符): {match[:200]}..."
                )
                try:
                    # 尝试解析每个匹配的块
                    parsed = json.loads(match)
                    if "summary" in parsed:
                        logger.info(
                            f"[MemoryProcessor]  成功从第 {i + 1} 个 JSON 块中解析数据"
                        )
                        data = parsed
                        break
                except json.JSONDecodeError:
                    continue

            # 如果没有找到完整的 JSON，尝试单独提取字段
            if data == self._get_default_structured_data(is_group_chat):
                logger.debug("[MemoryProcessor] 未找到完整 JSON，尝试提取单独字段")

                # 提取summary
                summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
                if summary_match:
                    data["summary"] = summary_match.group(1)
                    logger.debug(
                        f"[MemoryProcessor] 正则提取 summary: {data['summary'][:50]}..."
                    )

                # 提取importance
                importance_match = re.search(r'"importance"\s*:\s*([0-9.]+)', text)
                if importance_match:
                    data["importance"] = float(importance_match.group(1))
                    logger.debug(
                        f"[MemoryProcessor] 正则提取 importance: {data['importance']}"
                    )

                # 提取sentiment
                sentiment_match = re.search(r'"sentiment"\s*:\s*"(\w+)"', text)
                if sentiment_match:
                    data["sentiment"] = sentiment_match.group(1)
                    logger.debug(
                        f"[MemoryProcessor] 正则提取 sentiment: {data['sentiment']}"
                    )

                # 提取 topics 数组
                topics_match = re.search(r'"topics"\s*:\s*\[(.*?)\]', text, re.DOTALL)
                if topics_match:
                    topics_str = topics_match.group(1)
                    topics = re.findall(r'"([^"]+)"', topics_str)
                    data["topics"] = topics[:5]
                    logger.debug(f"[MemoryProcessor] 正则提取 topics: {data['topics']}")

                # 提取 key_facts 数组
                facts_match = re.search(r'"key_facts"\s*:\s*\[(.*?)\]', text, re.DOTALL)
                if facts_match:
                    facts_str = facts_match.group(1)
                    facts = re.findall(r'"([^"]+)"', facts_str)
                    data["key_facts"] = facts[:5]
                    logger.debug(
                        f"[MemoryProcessor] 正则提取 key_facts: {data['key_facts']}"
                    )

            logger.info(
                f"[MemoryProcessor] 正则提取完成，提取到的字段: {list(data.keys())}"
            )

        except Exception as e:
            logger.error(f"[MemoryProcessor]  正则提取失败: {e}", exc_info=True)

        return data

    def _build_storage_format(
        self,
        conversation_text: str,
        structured_data: Dict[str, Any],
        is_group_chat: bool,
    ) -> tuple[str, Dict[str, Any]]:
        """
        构建存储格式

        Args:
            conversation_text: 原始对话文本（已不再使用，保留参数以兼容旧代码）
            structured_data: 结构化数据
            is_group_chat: 是否为群聊

        Returns:
            (content, metadata) 元组
        """
        # content字段：只保留LLM生成的摘要（第一人称）
        summary = structured_data.get("summary", "")
        if summary:
            content = summary
        else:
            # 降级方案：如果没有摘要，使用简短的对话文本
            content = conversation_text[:200] + "..." if len(conversation_text) > 200 else conversation_text

        # metadata字段:存储结构化信息
        metadata = {
            "topics": structured_data.get("topics", []),
            "key_facts": structured_data.get("key_facts", []),
            "sentiment": structured_data.get("sentiment", "neutral"),
            "interaction_type": "group_chat" if is_group_chat else "private_chat",
        }

        if is_group_chat and "participants" in structured_data:
            metadata["participants"] = structured_data["participants"]

        return content, metadata

    def _ensure_list(self, value: Any) -> List[str]:
        """确保值是字符串列表"""
        if isinstance(value, list):
            return [str(item) for item in value if item]
        elif isinstance(value, str):
            return [value] if value else []
        else:
            return []

    def _validate_sentiment(self, sentiment: str) -> str:
        """验证情感值"""
        valid_sentiments = ["positive", "neutral", "negative"]
        sentiment = sentiment.lower()
        return sentiment if sentiment in valid_sentiments else "neutral"

    def _validate_importance(self, importance: Any) -> float:
        """验证重要性评分"""
        try:
            score = float(importance)
            return max(0.0, min(1.0, score))  # 限制在0-1之间
        except (ValueError, TypeError):
            return 0.5

    def _get_default_value(self, field: str) -> Any:
        """获取字段的默认值"""
        defaults = {
            "summary": "",
            "topics": [],
            "key_facts": [],
            "participants": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        return defaults.get(field, "")

    def _get_default_structured_data(self, is_group_chat: bool) -> Dict[str, Any]:
        """获取默认的结构化数据"""
        data = {
            "summary": "对话记录",
            "topics": [],
            "key_facts": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        if is_group_chat:
            data["participants"] = []
        return data

    def _create_fallback_memory(
        self, conversation_text: str, is_group_chat: bool
    ) -> tuple[str, Dict[str, Any], float]:
        """
        创建降级记忆(当LLM处理失败时)

        Args:
            conversation_text: 对话文本
            is_group_chat: 是否为群聊

        Returns:
            (content, metadata, importance) 元组
        """
        logger.warning("使用降级方案创建记忆")

        content = conversation_text
        metadata = {
            "topics": [],
            "key_facts": [],
            "sentiment": "neutral",
            "interaction_type": "group_chat" if is_group_chat else "private_chat",
        }
        importance = 0.5

        return content, metadata, importance
