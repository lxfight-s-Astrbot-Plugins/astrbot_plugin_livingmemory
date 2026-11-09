"""
MemoryProcessor 的解析、校验与格式化辅助函数

这些函数不持有实例状态，由 MemoryProcessor 调用以保持 LLM 编排逻辑清晰。
"""

import json
import re
from datetime import datetime
from typing import Any

from astrbot.api import logger

from ..base.exceptions import MemoryParseError
from ..models.conversation_models import Message


def try_fix_json(text: str) -> str:
    """
    尝试修复损坏的 JSON 字符串

    Args:
        text: 可能损坏的 JSON 字符串

    Returns:
        修复后的 JSON 字符串
    """
    fixed = text.strip()

    # 移除 markdown 代码块标记
    if fixed.startswith("```json"):
        fixed = fixed[7:]
    elif fixed.startswith("```"):
        fixed = fixed[3:]
    if fixed.endswith("```"):
        fixed = fixed[:-3]
    fixed = fixed.strip()

    # 按 JSON 字符串边界修复，保留字段之间合法的换行/缩进以及字符串中的括号。
    result: list[str] = []
    closing: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(fixed):
        if in_string:
            if ord(char) < 0x20:
                # 非法的裸控制字符仅在字符串内转义。
                if escaped:
                    result.append("\\")
                result.append(json.dumps(char)[1:-1])
                escaped = False
                continue
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "[{":
            closing.append("]" if char == "[" else "}")
        elif char in "]}" and closing and closing[-1] == char:
            closing.pop()
        elif char == "," and fixed[index + 1 :].lstrip().startswith(("]", "}")):
            continue
        result.append(char)

    if in_string:
        if escaped:
            result.append("\\")
        result.append('"')
    result.extend(reversed(closing))
    return "".join(result)


def parse_llm_response(response_text: str, is_group_chat: bool) -> dict[str, Any]:
    """
    解析 LLM 响应,提取JSON数据

    Args:
        response_text: LLM响应文本
        is_group_chat: 是否为群聊

    Returns:
        解析后的字典数据

    Raises:
        MemoryParseError: 响应无法解析为包含 summary 的结构化数据时抛出；
            调用方（反思存储任务）按可重试失败处理，不再回退占位内容入库。
    """
    logger.debug(f"[MemoryProcessor] 开始解析 LLM 响应，长度={len(response_text)}")

    try:
        # 先清理可能的markdown代码块标记
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()

        data = json.loads(cleaned_text)

        # 类型检查：确保解析结果是 dict
        if not isinstance(data, dict):
            raise ValueError(f"期望 dict 类型，实际为 {type(data).__name__}")

        logger.info("[MemoryProcessor] JSON 解析成功")
        return normalize_parsed_data(data, is_group_chat)

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[MemoryProcessor]  JSON 解析失败: {e}")
        logger.debug(
            f"[MemoryProcessor] 解析失败的内容（前200字符）: {response_text[:200]}"
        )

        # 尝试修复 JSON 后再解析
        logger.info("[MemoryProcessor] 尝试修复 JSON 后重新解析")
        try:
            fixed_data = json.loads(try_fix_json(response_text))
            if isinstance(fixed_data, dict):
                logger.info("[MemoryProcessor] JSON 修复后解析成功")
                return normalize_parsed_data(fixed_data, is_group_chat)
        except (json.JSONDecodeError, ValueError) as fix_err:
            logger.debug(f"[MemoryProcessor] JSON 修复后仍无法解析: {fix_err}")

        logger.info("[MemoryProcessor] 尝试使用正则表达式提取 JSON")
        fallback_data = extract_by_regex(response_text, is_group_chat)
        if fallback_data is None:
            raise MemoryParseError(
                f"LLM 响应无法解析为记忆 JSON（{type(e).__name__}: {e}）"
            ) from e
        return fallback_data
    except Exception as e:
        logger.error(f"[MemoryProcessor]  解析 LLM 响应时发生异常: {e}", exc_info=True)
        logger.debug(f"[MemoryProcessor] 异常发生时的响应内容: {response_text[:200]}")
        raise MemoryParseError(f"解析 LLM 响应失败: {e}") from e


def extract_by_regex(text: str, is_group_chat: bool) -> dict[str, Any] | None:
    """
    使用正则表达式从文本中提取结构化数据(备用方案)

    Args:
        text: 响应文本
        is_group_chat: 是否为群聊

    Returns:
        提取的结构化数据；完全无法提取到 summary 时返回 None，由调用方按
        解析失败处理（不再返回占位内容）。
    """
    logger.debug("[MemoryProcessor] 开始使用正则表达式提取结构化数据")
    data = get_default_structured_data(is_group_chat)
    extracted = False

    try:
        # 先尝试找到完整的 JSON 块
        json_matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)

        for i, match in enumerate(json_matches):
            try:
                # 尝试解析每个匹配的块
                parsed = json.loads(match)
                if "summary" in parsed:
                    logger.info(
                        f"[MemoryProcessor]  成功从第 {i + 1} 个 JSON 块中解析数据"
                    )
                    data = parsed
                    extracted = True
                    break
            except json.JSONDecodeError:
                continue

        # 如果没有找到完整的 JSON，尝试单独提取字段
        if not extracted:
            # 提取summary
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
            if summary_match:
                data["summary"] = summary_match.group(1)
                extracted = True

            # 提取importance
            importance_match = re.search(r'"importance"\s*:\s*([0-9.]+)', text)
            if importance_match:
                data["importance"] = float(importance_match.group(1))

            # 提取sentiment
            sentiment_match = re.search(r'"sentiment"\s*:\s*"(\w+)"', text)
            if sentiment_match:
                data["sentiment"] = sentiment_match.group(1)

            # 提取 topics 数组
            topics_match = re.search(r'"topics"\s*:\s*\[(.*?)\]', text, re.DOTALL)
            if topics_match:
                topics_str = topics_match.group(1)
                topics = re.findall(r'"([^"]+)"', topics_str)
                data["topics"] = topics[:5]

            # 提取 key_facts 数组
            facts_match = re.search(r'"key_facts"\s*:\s*\[(.*?)\]', text, re.DOTALL)
            if facts_match:
                facts_str = facts_match.group(1)
                facts = re.findall(r'"([^"]+)"', facts_str)
                data["key_facts"] = facts[:5]

        if extracted:
            logger.info(
                f"[MemoryProcessor] 正则提取完成，提取到的字段: {list(data.keys())}"
            )
        else:
            logger.warning("[MemoryProcessor] 正则提取未能获得任何可用字段")

    except Exception as e:
        logger.error(f"[MemoryProcessor]  正则提取失败: {e}", exc_info=True)
        return None

    return data if extracted else None


def normalize_parsed_data(data: dict, is_group_chat: bool) -> dict[str, Any]:
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
            logger.warning(f"[MemoryProcessor] LLM 响应缺少字段: {field}, 使用默认值")
            data[field] = get_default_value(field)

    data["summary"] = str(data.get("summary", ""))
    data["canonical_summary"] = str(data.get("canonical_summary") or "").strip()
    data["topics"] = ensure_list(data.get("topics", []))[:5]
    data["key_facts"] = ensure_list(data.get("key_facts", []))[:5]
    data["sentiment"] = validate_sentiment(data.get("sentiment", "neutral"))
    data["importance"] = validate_importance(data.get("importance", 0.5))
    # 可选字段：LLM 可声明本条总结取代了哪些旧记忆（不产出时保持缺失）
    if "superseded_memory_ids" in data:
        data["superseded_memory_ids"] = normalize_memory_ids(
            data["superseded_memory_ids"]
        )

    if is_group_chat:
        data["participants"] = ensure_list(data.get("participants", []))

    return data


def normalize_memory_ids(value: Any) -> list[int]:
    """把 LLM 输出的记忆 id 列表归一化为去重后的正整数列表。"""
    if not isinstance(value, (list, tuple)):
        return []
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def ensure_list(value: Any) -> list[str]:
    """确保值是字符串列表"""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    elif isinstance(value, str):
        return [value] if value else []
    else:
        return []


def validate_sentiment(sentiment: str) -> str:
    """验证情感值"""
    valid_sentiments = ["positive", "neutral", "negative"]
    sentiment = sentiment.lower()
    return sentiment if sentiment in valid_sentiments else "neutral"


def validate_importance(importance: Any) -> float:
    """验证重要性评分"""
    try:
        score = float(importance)
        return max(0.0, min(1.0, score))  # 限制在0-1之间
    except (ValueError, TypeError):
        return 0.5


def get_default_value(field: str) -> Any:
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


def get_default_structured_data(is_group_chat: bool) -> dict[str, Any]:
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


def validate_summary_quality(structured_data: dict[str, Any]) -> str:
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


def build_storage_format(
    fallback_excerpt: str,
    structured_data: dict[str, Any],
    is_group_chat: bool,
) -> tuple[str, dict[str, Any]]:
    """
    构建存储格式

    Args:
        fallback_excerpt: 当摘要为空时使用的对话摘录
        structured_data: 结构化数据
        is_group_chat: 是否为群聊

    Returns:
        (content, metadata) 元组
    """
    summary = str(structured_data.get("summary", "")).strip()
    key_facts = structured_data.get("key_facts", [])

    # 检索内容恒为 summary + key_facts 的富文本：content 是 BM25/FAISS 的
    # 索引语料，也是 Agent 主动召回工具直接返回给 LLM 的内容，必须保证信息
    # 密度。不依赖模型输出的压缩摘要，也不退化为纯事实的机械拼接。
    # （自动注入链路优先使用 metadata 中的 persona_summary，不受此影响。）
    rich_parts = [summary] if summary else []
    if key_facts:
        rich_parts.append("；".join(str(f) for f in key_facts[:5] if f))
    rich_content = " | ".join(rich_parts)

    content = rich_content if rich_content else fallback_excerpt

    # canonical_summary：自定义提示词若输出了该字段则保留（供图抽取等使用），
    # 否则回退为与 content 一致的富文本，保持 v2 metadata 结构兼容。
    canonical_summary = str(structured_data.get("canonical_summary") or "").strip()
    if not canonical_summary:
        canonical_summary = rich_content

    # metadata字段:存储结构化信息
    # 注意：不要在这里设置 create_time 和 last_access_time
    # 这些字段会由 MemoryEngine.add_memory() 自动添加
    metadata = {
        "topics": structured_data.get("topics", []),
        "key_facts": key_facts,
        "sentiment": structured_data.get("sentiment", "neutral"),
        "interaction_type": "group_chat" if is_group_chat else "private_chat",
        # 双通道：canonical_summary 供图抽取等中性文本消费方使用，
        # persona_summary 保留人格风格摘要供面板展示
        "canonical_summary": canonical_summary,
        "persona_summary": summary,
        "summary_schema_version": "v2",
        # summary_quality 由 process_conversation 中的 SummaryValidator 覆盖写入
    }
    superseded_ids = structured_data.get("superseded_memory_ids")
    if superseded_ids:
        metadata["superseded_memory_ids"] = list(superseded_ids)

    if is_group_chat and "participants" in structured_data:
        metadata["participants"] = structured_data["participants"]

    return content, metadata


def format_conversation(messages: list[Message]) -> str:
    """
    格式化对话历史为文本

    Args:
        messages: 消息列表(Message对象)

    Returns:
        格式化后的对话文本
    """

    formatted_lines = []
    for i, msg in enumerate(messages):
        logger.debug(
            f"[_format_conversation] 消息#{i}: "
            f"sender_id={msg.sender_id}, sender_name={msg.sender_name}, "
            f"role={msg.role}, group_id={msg.group_id}"
        )

        content_text = message_content_to_text(msg.content)
        sender_info = format_sender_info(msg)
        formatted_line = f"{sender_info} {content_text}".rstrip()
        formatted_lines.append(formatted_line)
        if msg.group_id:
            logger.debug(
                f"[_format_conversation] 消息#{i} 格式化结果(群聊): {formatted_line[:100]}..."
            )
        else:
            logger.debug(
                f"[_format_conversation] 消息#{i} 格式化结果(私聊): {sender_info[:50]}..."
            )
    return "\n".join(formatted_lines)


def extract_participant_identities(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """Build stable graph identities from message sender IDs, not LLM names."""
    identities: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.role == "system":
            continue
        sender_id = str(message.sender_id or "").strip()
        if not sender_id:
            continue
        platform = str(message.platform or "unknown").strip().lower() or "unknown"
        identity_key = f"{platform}:{sender_id}"
        display_name = str(message.sender_name or sender_id).strip() or sender_id
        is_bot = bool(
            message.metadata.get("is_bot_message", False) or message.role == "assistant"
        )

        identity = identities.setdefault(
            identity_key,
            {
                "identity_key": identity_key,
                "sender_id": sender_id,
                "platform": platform,
                "display_name": display_name,
                "aliases": [],
                "is_bot": is_bot,
            },
        )
        identity["display_name"] = display_name
        identity["is_bot"] = bool(identity["is_bot"] or is_bot)
        if display_name not in identity["aliases"]:
            identity["aliases"].append(display_name)

    return list(identities.values())


def format_sender_info(msg: Message) -> str:
    time_str = datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    display_name = msg.sender_name if msg.sender_name else msg.sender_id or "未知"
    is_bot = msg.metadata.get("is_bot_message", False) or msg.role == "assistant"
    if is_bot:
        return f"[Bot: {display_name} | ID: {msg.sender_id} | {time_str}]"
    return f"[{display_name} | ID: {msg.sender_id} | {time_str}]"


def message_content_to_text(content: Any) -> str:
    return Message.content_to_text(content)


def apply_source_time_tags(
    content: str,
    metadata: dict[str, Any],
    messages: list[Message],
    include_source_time_tags: bool,
) -> str:
    """Attach source dates without asking the LLM to infer them."""
    if not include_source_time_tags or not messages:
        return content

    timestamps = sorted(float(message.timestamp) for message in messages)
    start = datetime.fromtimestamp(timestamps[0]).astimezone()
    end = datetime.fromtimestamp(timestamps[-1]).astimezone()
    dates = sorted(
        {datetime.fromtimestamp(value).strftime("%Y-%m-%d") for value in timestamps}
    )
    label = dates[0] if len(dates) == 1 else f"{dates[0]} - {dates[-1]}"

    metadata["time_tags"] = dates
    metadata["source_time_start"] = start.isoformat()
    metadata["source_time_end"] = end.isoformat()
    metadata["source_time_label"] = label
    return content
