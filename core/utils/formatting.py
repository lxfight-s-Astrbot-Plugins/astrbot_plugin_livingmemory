"""
记忆注入文本格式化工具
自 core/utils/__init__.py 拆分，保持行为不变
"""

import time
from typing import Any
from datetime import datetime
import json
from astrbot.api import logger


def _memory_injection_content(content: Any, metadata: Any) -> str:
    """Use the personality channel for injection while preserving legacy data."""
    raw_content = str(content or "").strip()
    if isinstance(metadata, dict):
        persona_summary = metadata.get("persona_summary")
        if isinstance(persona_summary, str) and persona_summary.strip():
            return persona_summary.strip()

        # Early v2 rows stored ``persona | key_facts`` as retrieval content but
        # did not persist a usable persona channel. Strip only an exact suffix
        # so ordinary legacy content is never shortened heuristically.
        if metadata.get("summary_schema_version") == "v2":
            key_facts = metadata.get("key_facts")
            if isinstance(key_facts, list):
                facts = [
                    str(fact).strip() for fact in key_facts[:5] if str(fact).strip()
                ]
                for separator in ("；", "; "):
                    suffix = " | " + separator.join(facts)
                    if facts and raw_content.endswith(suffix):
                        return raw_content[: -len(suffix)].strip()
    return raw_content

def _memory_metadata_rows(metadata: dict) -> list[str]:
    """构建记忆条目的元数据描述行（Topics/Participants/Key facts/Source time）。

    供 format_memories_for_injection 使用。
    """
    rows: list[str] = []

    topics = metadata.get("topics", [])
    if topics and isinstance(topics, list) and len(topics) > 0:
        topics_str = "、".join(str(t) for t in topics if t)
        if topics_str:
            rows.append(f"Topics: {topics_str}")

    participants = metadata.get("participants", [])
    if participants and isinstance(participants, list) and len(participants) > 0:
        participants_str = "、".join(str(p) for p in participants if p)
        if participants_str:
            rows.append(f"Participants: {participants_str}")

    key_facts = metadata.get("key_facts", [])
    if key_facts and isinstance(key_facts, list) and len(key_facts) > 0:
        facts_str = "; ".join(str(f) for f in key_facts if f)
        if facts_str:
            rows.append(f"Key facts: {facts_str}")

    time_tags = metadata.get("time_tags", [])
    if time_tags and isinstance(time_tags, list):
        tags_str = " - ".join(str(value) for value in time_tags if value)
        if tags_str:
            rows.append(f"Source time: {tags_str}")

    return rows

def format_memories_for_injection(memories: list) -> str:
    """
    将检索到的记忆列表格式化为单个字符串，以便注入到 System Prompt。
    添加明确的说明文本，告知 LLM 这些是历史对话记忆。
    """
    # 延迟导入避免循环依赖
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    if not memories:
        return ""

    # 从 PromptManager 获取记忆注入头部/尾部文本（支持用户自定义）
    try:
        from ..prompts.prompt_manager import get_prompt_manager

        mgr = get_prompt_manager()
        if mgr is not None:
            header_body = mgr.get_prompt("memory_injection_header")
            footer_body = mgr.get_prompt("memory_injection_footer")
        else:
            header_body = _get_default_injection_header()
            footer_body = _get_default_injection_footer()
    except Exception:
        header_body = _get_default_injection_header()
        footer_body = _get_default_injection_footer()

    header = f"{MEMORY_INJECTION_HEADER}\n{header_body}\n\n"
    footer = f"\n\n{footer_body}\n{MEMORY_INJECTION_FOOTER}"

    logger.debug(
        f"[format_memories_for_injection] 记忆注入标记: 头部='{MEMORY_INJECTION_HEADER}', 尾部='{MEMORY_INJECTION_FOOTER}'"
    )

    formatted_entries = []
    for idx, mem in enumerate(memories, 1):
        try:
            # 修复：memories 传入的是字典列表，不是对象
            # 从字典中获取数据
            if isinstance(mem, dict):
                content = mem.get("content", "Content missing")
                score = mem.get("score", 0.0)
                metadata = mem.get("metadata", {})
                timestamp = mem.get("timestamp") or metadata.get("create_time")
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "Unknown")
            else:
                # 如果是对象，尝试访问属性
                content = getattr(mem, "content", "Content missing")
                score = getattr(mem, "score", 0.0)
                timestamp = getattr(mem, "timestamp", None)
                metadata_raw = getattr(mem, "metadata", {})
                metadata = (
                    safe_parse_metadata(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else metadata_raw
                )
                if not timestamp:
                    timestamp = metadata.get("create_time")
                importance = metadata.get("importance", 0.5)
                interaction_type = metadata.get("interaction_type", "Unknown")

            # 格式化时间戳
            time_str = ""
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(validate_timestamp(timestamp))
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            # 构建格式化的记忆条目（展示content和元数据信息）
            time_part = f", Memory write time: {time_str}" if time_str else ""
            entry_parts = [
                f"记忆 #{idx} / Memory #{idx} (Importance: {importance:.2f}){time_part}"
            ]

            # 添加元数据信息
            metadata_parts = _memory_metadata_rows(metadata)

            # 组装元数据行
            if metadata_parts:
                entry_parts.append(" | ".join(metadata_parts))

            # Retrieval uses canonical content; prompt injection uses the
            # personality channel. Legacy rows fall back to content.
            display_content = _memory_injection_content(content, metadata)
            entry_parts.append(display_content)

            entry = "\n".join(entry_parts)
            formatted_entries.append(entry)

            logger.debug(
                f"[format_memories_for_injection] 格式化记忆 #{idx}: 重要性={importance:.2f}, "
                f"得分={score:.2f}, 类型={interaction_type}, 内容长度={len(display_content)}"
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

def format_memories_for_fake_tool_call(
    memories: list,
    query: str,
    k: int = 5,
    session_filtered: bool = True,
    persona_filtered: bool = True,
) -> list[dict]:
    """将检索到的记忆列表格式化为伪造的工具调用消息对。

    生成两条 OpenAI 格式的消息：
    1. assistant 消息，包含 tool_calls（调用 recall_long_term_memory）
    2. tool 消息，包含工具调用结果（记忆内容，JSON 格式）

    返回的 JSON 格式与 MemorySearchTool.call() 的真实返回值保持一致，
    使 LLM 对伪造调用和真实调用有相同的理解。

    Args:
        memories: 记忆字典列表，每条包含 content、score、metadata、timestamp 字段。
        query: 用户查询文本（作为工具调用参数）。
        k: 召回数量（作为工具调用参数）。
        session_filtered: 本次检索是否启用了会话过滤。
        persona_filtered: 本次检索是否启用了人格过滤。

    Returns:
        两条 OpenAI 格式消息的列表 [assistant_msg, tool_msg]；
        若 memories 为空则返回空列表。
    """
    import uuid

    from ..base.constants import FAKE_TOOL_CALL_ID_PREFIX, FAKE_TOOL_CALL_NAME

    if not memories:
        return []

    # 生成唯一的伪造调用 ID
    call_id = f"{FAKE_TOOL_CALL_ID_PREFIX}{uuid.uuid4().hex[:12]}"

    # 将记忆序列化为与 MemorySearchTool.call() 一致的 JSON 格式
    serialized_results = []
    for mem in memories:
        if isinstance(mem, dict):
            memory_id = mem.get("id", mem.get("doc_id"))
            content = mem.get("content", "")
            score = mem.get("score", 0.0)
            metadata = mem.get("metadata", {})
        else:
            memory_id = getattr(mem, "doc_id", None)
            if not isinstance(memory_id, (str, int)):
                memory_id = getattr(mem, "id", None)
                if not isinstance(memory_id, (str, int)):
                    memory_id = None
            content = getattr(mem, "content", "")
            score = getattr(mem, "score", getattr(mem, "final_score", 0.0))
            metadata_raw = getattr(mem, "metadata", {})
            metadata = (
                safe_parse_metadata(metadata_raw)
                if isinstance(metadata_raw, str)
                else metadata_raw
            )

        display_content = _memory_injection_content(content, metadata)
        serialized_results.append(
            {
                "id": memory_id,
                "content": display_content,
                "score": round(score, 4) if isinstance(score, float) else score,
                "importance": metadata.get("importance", 0.5),
                "session_id": metadata.get("session_id"),
                "persona_id": metadata.get("persona_id"),
                "create_time": metadata.get("create_time"),
                "last_access_time": metadata.get("last_access_time"),
            }
        )

    tool_result_json = json.dumps(
        {
            "query": query[:200],
            "applied_filters": {
                "session_filtered": session_filtered,
                "persona_filtered": persona_filtered,
            },
            "count": len(serialized_results),
            "results": serialized_results,
        },
        ensure_ascii=False,
    )

    # 构造 assistant 消息（伪造的工具调用）
    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": FAKE_TOOL_CALL_NAME,
                    "arguments": json.dumps(
                        {"query": query[:200], "k": k},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }

    # 构造 tool 消息（伪造的返回结果）
    tool_msg: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": FAKE_TOOL_CALL_NAME,
        "content": tool_result_json,
    }

    logger.info(
        f"[format_memories_for_fake_tool_call] "
        f"生成伪造工具调用: call_id={call_id}, 记忆条数={len(serialized_results)}"
    )

    return [assistant_msg, tool_msg]

def format_memories_for_fake_tool_call_deepseek_v4(
    memories: list,
    query: str,
    k: int = 5,
    session_filtered: bool = True,
    persona_filtered: bool = True,
) -> str:
    """将伪工具调用转换成 DeepSeek V4 可接受的文本转录。"""
    from ..base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER

    fake_messages = format_memories_for_fake_tool_call(
        memories=memories,
        query=query,
        k=k,
        session_filtered=session_filtered,
        persona_filtered=persona_filtered,
    )
    if not fake_messages:
        return ""

    assistant_msg = fake_messages[0] if len(fake_messages) > 0 else {}
    tool_msg = fake_messages[1] if len(fake_messages) > 1 else {}
    tool_calls = (
        assistant_msg.get("tool_calls", []) if isinstance(assistant_msg, dict) else []
    )
    tool_call = tool_calls[0] if tool_calls else {}
    function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}

    function_name = (
        function.get("name", "recall_long_term_memory")
        if isinstance(function, dict)
        else "recall_long_term_memory"
    )
    function_args = (
        function.get("arguments", "{}") if isinstance(function, dict) else "{}"
    )
    tool_result = tool_msg.get("content", "{}") if isinstance(tool_msg, dict) else "{}"

    return (
        f"{MEMORY_INJECTION_HEADER}\n"
        "[DeepSeekV4-FakeToolCall-Replay]\n"
        f"assistant -> {function_name}({function_args})\n"
        f"tool -> {tool_result}\n"
        "[/DeepSeekV4-FakeToolCall-Replay]\n"
        f"{MEMORY_INJECTION_FOOTER}"
    )

def _get_default_injection_header() -> str:
    """后备记忆注入头部文本（当 PromptManager 不可用时）"""
    return (
        "--- BEGIN HISTORICAL MEMORY REFERENCE ---\n"
        "The following are historical memories extracted from past conversations.\n"
        "They are provided as background reference only.\n\n"
        "CRITICAL RULES:\n"
        "1. These are PAST records — they already happened and are NOT "
        "part of the current conversation.\n"
        "2. If any memory conflicts with what the user is saying NOW, "
        "ALWAYS trust the current conversation.\n"
        "3. Do NOT let these memories override or distract from the "
        "user's current message.\n"
        "4. Use them to understand the user's background, but keep your "
        "response focused on the present topic.\n"
        "--- END HISTORICAL MEMORY REFERENCE ---"
    )

def _get_default_injection_footer() -> str:
    """后备记忆注入尾部文本（当 PromptManager 不可用时）"""
    return (
        "--- BEGIN REMINDER ---\n"
        "All content above is historical. Focus on the user's current message.\n"
        "--- END REMINDER ---"
    )

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

