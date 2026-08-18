"""
MemoryProcessor 的 MemoryProcessorParseMixin 拆分模块
自动从 core/processors/memory_processor.py 拆分，保持行为不变
"""

from typing import Any
import json
from astrbot.api import logger
import re


class MemoryProcessorParseMixin:
    """MemoryProcessor 拆分模块：MemoryProcessorParseMixin"""
    def _parse_llm_response(
        self, response_text: str, is_group_chat: bool
    ) -> dict[str, Any]:
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

            # 类型检查：确保解析结果是 dict
            if not isinstance(data, dict):
                logger.warning(
                    f"[MemoryProcessor] JSON 解析结果不是 dict，类型为 {type(data).__name__}"
                )
                raise ValueError(f"期望 dict 类型，实际为 {type(data).__name__}")

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

            data["canonical_summary"] = str(
                data.get("canonical_summary") or ""
            ).strip()

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

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[MemoryProcessor]  JSON 解析失败: {e}")
            logger.debug(
                f"[MemoryProcessor] 解析失败的内容（前200字符）: {response_text[:200]}"
            )

            # 尝试修复 JSON 后再解析
            logger.info("[MemoryProcessor] 尝试修复 JSON 后重新解析")
            try:
                fixed_text = self._try_fix_json(response_text)
                data = json.loads(fixed_text)
                if isinstance(data, dict):
                    logger.info("[MemoryProcessor] JSON 修复后解析成功")
                    return self._normalize_parsed_data(data, is_group_chat)
            except (json.JSONDecodeError, ValueError) as fix_err:
                logger.debug(f"[MemoryProcessor] JSON 修复后仍无法解析: {fix_err}")

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

    def _extract_by_regex(self, text: str, is_group_chat: bool) -> dict[str, Any]:
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

    def _normalize_parsed_data(self, data: dict, is_group_chat: bool) -> dict[str, Any]:
        """
        规范化解析后的数据（补充缺失字段、类型转换）

        Args:
            data: 解析后的原始字典
            is_group_chat: 是否为群聊

        Returns:
            规范化后的字典
        """
        required_fields = ["summary", "topics", "key_facts", "sentiment", "importance"]
        if is_group_chat:
            required_fields.append("participants")

        for field in required_fields:
            if field not in data:
                data[field] = self._get_default_value(field)

        data["summary"] = str(data.get("summary", ""))
        data["canonical_summary"] = str(data.get("canonical_summary") or "").strip()
        data["topics"] = self._ensure_list(data.get("topics", []))[:5]
        data["key_facts"] = self._ensure_list(data.get("key_facts", []))[:5]
        data["sentiment"] = self._validate_sentiment(data.get("sentiment", "neutral"))
        data["importance"] = self._validate_importance(data.get("importance", 0.5))

        if is_group_chat:
            data["participants"] = self._ensure_list(data.get("participants", []))

        return data

    def _ensure_list(self, value: Any) -> list[str]:
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
            "canonical_summary": "",
            "topics": [],
            "key_facts": [],
            "participants": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        return defaults.get(field, "")

    def _get_default_structured_data(self, is_group_chat: bool) -> dict[str, Any]:
        """获取默认的结构化数据"""
        data = {
            "summary": "对话记录",
            "canonical_summary": "",
            "topics": [],
            "key_facts": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        if is_group_chat:
            data["participants"] = []
        return data

    def _validate_summary_quality(self, structured_data: dict[str, Any]) -> str:
        """
        校验总结质量，返回质量等级。

        检查规则：
        1. summary 不能为空或过短（< 10 字符）
        2. key_facts 至少有 1 条
        3. importance 在合法范围内
        4. summary 不含泛化词（"某用户"、"有人"等）

        Returns:
            "normal" 或 "low"
        """
        summary = structured_data.get("summary", "")
        key_facts = structured_data.get("key_facts", [])
        importance = structured_data.get("importance", 0.5)

        if not summary or len(summary.strip()) < 10:
            return "low"
        if not key_facts:
            return "low"
        if not isinstance(importance, (int, float)) or not (0.0 <= importance <= 1.0):
            return "low"

        # 泛化词检测
        generic_terms = [
            "某用户",
            "有人",
            "某人",
            "用户说",
            "对方说",
            "群成员",
            "某群成员",
        ]
        if any(term in summary for term in generic_terms):
            return "low"

        return "normal"

    def build_memory_from_structured_data(
        self,
        structured_data: dict[str, Any],
        is_group_chat: bool = False,
        fallback_excerpt: str = "",
    ) -> tuple[str, dict[str, Any], float]:
        """复用自动总结流程，将结构化数据转换为标准记忆存储格式。"""
        # 与自动总结路径保持一致：先校验质量，再规范化。
        # 这样原始 importance 越界等异常仍会被判为 low quality。
        quality = self._validate_summary_quality(structured_data)
        normalized = self._normalize_parsed_data(structured_data, is_group_chat)
        normalized["_quality"] = quality

        content, metadata = self._build_storage_format(
            fallback_excerpt or normalized.get("summary", ""),
            normalized,
            is_group_chat,
        )
        metadata["summary_quality"] = quality
        return (
            content,
            metadata,
            self._validate_importance(normalized.get("importance")),
        )
