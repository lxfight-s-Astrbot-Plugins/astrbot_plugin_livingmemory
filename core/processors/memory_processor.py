"""
记忆处理器 - 使用LLM将对话历史处理为结构化记忆
"""

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..astrbot_compat import get_using_provider
from ..base.usage_metrics import record_llm_usage
from ..models.conversation_models import Message
from ..models.memory_atom import MemoryAtom
from .atom_classifier import classify_atoms
from .memory_processor_parse import (
    apply_source_time_tags,
    build_storage_format,
    ensure_list,
    extract_participant_identities,
    format_conversation,
    normalize_parsed_data,
    parse_llm_response,
    try_fix_json,
    validate_importance,
    validate_summary_quality,
)

# 瞬时错误类型名标记（httpx/openai/aiohttp 等连接与协议异常）
_TRANSIENT_ERROR_TYPE_MARKERS = (
    "timeout",
    "connect",
    "network",
    "transport",
    "protocol",
)
# 瞬时错误消息标记：没有状态码/异常类型可用时兜底匹配
_TRANSIENT_ERROR_MESSAGE_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "temporarily unavailable",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "connect error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "network error",
)


def _extract_http_status(exc: BaseException) -> int | None:
    """从异常对象上提取 HTTP 状态码（兼容 httpx/openai/aiohttp 风格）。"""
    candidates = (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "response", None), "status", None),
    )
    for value in candidates:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _is_transient_llm_error(exc: BaseException) -> bool:
    """判断 LLM 调用异常是否为可重试的瞬时错误。

    仅超时、HTTP 429/5xx 与连接类错误可重试；解析失败、校验失败、
    参数/鉴权错误等立即失败，避免同一错误反复消耗 LLM 调用额度。
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = _extract_http_status(exc)
    if status is not None:
        return status == 429 or 500 <= status < 600
    type_names = {cls.__name__.lower() for cls in type(exc).__mro__}
    if any(
        marker in type_name
        for type_name in type_names
        for marker in _TRANSIENT_ERROR_TYPE_MARKERS
    ):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MESSAGE_MARKERS)


class MemoryProcessor:
    """
    记忆处理器

    使用LLM将对话历史转换为结构化记忆。
    支持私聊和群聊两种场景的不同处理策略。
    """

    def __init__(
        self,
        context=None,
        llm_provider: Any = None,
        config: dict[str, Any] | None = None,
    ):
        """
        初始化记忆处理器

        Args:
            context: AstrBot上下文,用于获取人格管理器
            llm_provider: LLM Provider 实例或 Provider ID 字符串。
                          传入实例时直接使用（测试用）；传入字符串时动态解析。
                          留空则使用AstrBot默认Provider。
            config: 记忆处理器配置。
        """
        self.context = context
        self._llm_provider = llm_provider
        self.config = config or {}
        # 记录已确认只接受流式请求的 Provider ID，auto 模式后续直接走流式
        self._stream_only_providers: set[str] = set()

        # 加载提示词模板
        self._load_prompts()

    async def _get_current_llm_provider(self):
        """动态解析LLM Provider以避免持有过期引用

        AstrBot可能在运行期间重新创建Provider实例（例如配置变更后），
        旧的Provider实例内部的httpx client会被关闭，导致
        RuntimeError: Cannot send a request, as the client has been closed.
        因此每次调用前都从AstrBot上下文重新获取当前有效的Provider。
        """
        if not self.context:
            # 无 context 时直接返回传入的 provider 实例（测试路径）
            if self._llm_provider is not None and not isinstance(
                self._llm_provider, str
            ):
                return self._llm_provider
            return None

        # 如果传入的是 provider 实例（非字符串），直接使用（测试路径）
        if self._llm_provider is not None and not isinstance(self._llm_provider, str):
            return self._llm_provider

        # 优先使用配置中指定的Provider ID（字符串）
        if isinstance(self._llm_provider, str) and self._llm_provider:
            try:
                provider = self.context.get_provider_by_id(self._llm_provider)
                if provider:
                    return provider
            except Exception:
                pass

        # 回退到AstrBot当前默认Provider
        try:
            provider = await get_using_provider(self.context)
            if provider:
                return provider
        except Exception:
            pass

        return None

    def _load_prompts(self) -> None:
        """从 PromptManager 加载提示词模板（支持用户自定义覆盖）"""
        try:
            from ..prompts.prompt_manager import get_prompt_manager

            mgr = get_prompt_manager()
            if mgr is not None:
                self.private_chat_prompt = mgr.get_prompt("private_chat_prompt")
                self.group_chat_prompt = mgr.get_prompt("group_chat_prompt")
                logger.info("[MemoryProcessor] 通过 PromptManager 加载提示词模板成功")
            else:
                self._load_prompts_fallback()
        except Exception as e:
            logger.error(f"[MemoryProcessor] 通过 PromptManager 加载提示词失败: {e}")
            self._load_prompts_fallback()

    def _get_chat_prompt(self, is_group_chat: bool) -> str:
        """每次处理时从 PromptManager 实时读取，确保 WebUI 保存后立即生效。"""
        try:
            from ..prompts.prompt_manager import get_prompt_manager

            mgr = get_prompt_manager()
            if mgr is not None:
                prompt_id = (
                    "group_chat_prompt" if is_group_chat else "private_chat_prompt"
                )
                return mgr.get_prompt(prompt_id)
        except Exception:
            pass
        return self.group_chat_prompt if is_group_chat else self.private_chat_prompt

    def _load_prompts_fallback(self) -> None:
        """后备加载：直接从文件读取提示词"""
        prompt_dir = Path(__file__).parent.parent / "prompts"

        try:
            private_prompt_file = prompt_dir / "private_chat_prompt.txt"
            with open(private_prompt_file, encoding="utf-8") as f:
                self.private_chat_prompt = f.read()

            group_prompt_file = prompt_dir / "group_chat_prompt.txt"
            with open(group_prompt_file, encoding="utf-8") as f:
                self.group_chat_prompt = f.read()

            logger.info("[MemoryProcessor] 提示词模板加载成功（后备模式）")

        except Exception as e:
            logger.error(f"[MemoryProcessor] 加载提示词模板失败: {e}")
            self.private_chat_prompt = """分析以下对话并生成JSON格式的记忆:
{conversation}

输出格式:
{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], "sentiment": "neutral", "importance": 0.5}
"""
            self.group_chat_prompt = """分析以下群聊对话并生成JSON格式的记忆:
{conversation}

输出格式:
{"summary": "摘要", "topics": ["主题"], "key_facts": ["事实"], "participants": ["参与者"], "sentiment": "neutral", "importance": 0.5}
"""

    async def _build_system_prompt_with_persona(self, persona_id: str | None) -> str:
        """
        构建包含人格提示的 system_prompt

        Args:
            persona_id: 人格ID

        Returns:
            str: 包含人格提示的 system_prompt
        """
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 尝试从 PromptManager 获取基础 system prompt
        try:
            from ..prompts.prompt_manager import get_prompt_manager

            mgr = get_prompt_manager()
            if mgr is not None:
                base_prompt = mgr.get_prompt("memory_system_prompt_base").replace(
                    "{current_date}", current_date
                )
            else:
                base_prompt = self._build_base_prompt_fallback(current_date)
        except Exception:
            base_prompt = self._build_base_prompt_fallback(current_date)

        if not persona_id:
            logger.debug("[MemoryProcessor] 未指定人格ID，使用基础提示词")
            return base_prompt

        if not self.context:
            logger.debug("[MemoryProcessor] Context 未设置，使用基础提示词")
            return base_prompt

        try:
            persona_manager = getattr(self.context, "persona_manager", None)
            if not persona_manager:
                logger.warning(
                    "[MemoryProcessor] persona_manager 不可用，使用基础提示词"
                )
                return base_prompt

            persona = await persona_manager.get_persona(persona_id)
            if not persona:
                logger.warning(
                    f"[MemoryProcessor] 人格 '{persona_id}' 不存在，使用基础提示词"
                )
                return base_prompt

            if not persona.system_prompt:
                logger.debug(
                    f"[MemoryProcessor] 人格 '{persona_id}' 无 system_prompt，使用基础提示词"
                )
                return base_prompt

            persona_prompt = persona.system_prompt.strip()
            if not persona_prompt:
                logger.debug(
                    f"[MemoryProcessor] 人格 '{persona_id}' 的 system_prompt 为空，使用基础提示词"
                )
                return base_prompt

            logger.info(
                f"[MemoryProcessor] 成功加载人格 '{persona_id}' 的提示词 "
                f"(长度={len(persona_prompt)}字符)"
            )
            logger.debug(f"[MemoryProcessor] 人格提示词预览: {persona_prompt[:100]}...")

            # 使用 PromptManager 模板构建增强提示词
            try:
                if mgr is not None:
                    enhanced_template = mgr.get_prompt(
                        "memory_system_prompt_with_persona"
                    )
                    enhanced_prompt = (
                        enhanced_template.replace("{base_prompt}", base_prompt)
                        .replace("{persona_prompt}", persona_prompt)
                        .replace("{current_date}", current_date)
                    )
                else:
                    enhanced_prompt = self._build_enhanced_prompt_fallback(
                        base_prompt, persona_prompt, current_date
                    )
            except Exception:
                enhanced_prompt = self._build_enhanced_prompt_fallback(
                    base_prompt, persona_prompt, current_date
                )

            return enhanced_prompt

        except ValueError as e:
            logger.warning(f"[MemoryProcessor] 人格 '{persona_id}' 不存在: {e}")
            return base_prompt
        except Exception as e:
            logger.error(
                f"[MemoryProcessor] 获取人格提示词时发生错误: {e}", exc_info=True
            )
            return base_prompt

    @staticmethod
    def _build_base_prompt_fallback(current_date: str) -> str:
        """后备基础 system prompt（当 PromptManager 不可用时）"""
        return (
            "你正在总结对话记忆。请严格按照JSON格式输出。\n"
            "重要: 请将对话中出现的相对时间表达（如\u201c今天\u201d、"
            "\u201c明天\u201d、\u201c昨天\u201d、"
            "\u201c下周\u201d、\u201c上个月\u201d等）"
            "按该条消息的发送时间转换为具体日期后再写入记忆。"
            "只有消息缺少时间时才参考本次请求的当前日期时间。"
            "跨日期消息须分别确定时间，以便未来查阅时仍能准确理解时间信息。"
        )

    @staticmethod
    def _build_enhanced_prompt_fallback(
        base_prompt: str, persona_prompt: str, current_date: str
    ) -> str:
        """后备增强 system prompt（当 PromptManager 不可用时）"""
        return (
            f"{base_prompt}\n\n"
            f"## 你的人格设定\n"
            f"{persona_prompt}\n\n"
            f"## 记忆总结要求\n"
            f"在总结对话记忆时,你需要:\n"
            f"1. **保持你的人格特色**: 使用符合上述人格设定的语气、用词习惯和表达方式\n"
            f'2. **第一人称视角**: 以"我"的视角回顾对话,不要说"bot"、"助手"等第三人称\n'
            f"3. **体现你的关注点**: 根据你的人格特点,侧重记录你会关注的信息\n"
            f"4. **自然真实**: 让记忆读起来像是你本人在回忆这段对话,而不是机械的客观描述\n"
            "5. **时间转换**: 按每条消息的发送时间将相对时间（今天、明天、下周等）转换为具体日期；只有消息缺少时间时才参考本次请求的当前日期时间\n\n"
            f"例如:\n"
            f'- 如果你是活泼可爱的性格,记忆中可以使用"呀"、"呢"、"~"等语气词\n'
            f"- 如果你是专业严谨的性格,记忆应该用词准确、逻辑清晰、格式规范\n"
            f"- 如果你是幽默风趣的性格,记忆中可以包含轻松的表达和有趣的观察"
        )

    async def _call_llm_with_retry(
        self, prompt: str, system_prompt: str, max_retries: int = 2
    ) -> str:
        """
        带指数退避的 LLM 调用

        仅对瞬时错误（超时、HTTP 429/5xx、连接错误）重试；解析/校验失败、
        参数/鉴权错误立即抛出，避免同一失败反复消耗 LLM 调用。默认共 2 次
        尝试（首次调用 + 1 次重试）。

        按配置 `llm_stream_mode` 选择调用方式（issue #265）：
        `never` 始终非流式（旧行为），`always` 始终流式，`auto` 优先非流式，
        遇到"不支持非流式请求"错误时自动改用流式并记住该 Provider。

        Args:
            prompt: 提示词
            system_prompt: 系统提示词
            max_retries: 最大尝试次数（含首次调用），默认 2

        Returns:
            LLM 响应文本

        Raises:
            RuntimeError: Provider 不可用，或流式调用未返回完整结果
        """
        # 未知取值（含配置缺失）安全回退为 auto
        stream_mode = self.config.get("llm_stream_mode", "auto")
        if stream_mode not in ("auto", "always", "never"):
            stream_mode = "auto"

        last_error = None
        for attempt in range(max_retries):
            try:
                provider = await self._get_current_llm_provider()
                if not provider:
                    raise RuntimeError("LLM Provider 不可用")

                # 用 Provider ID 记住只接受流式的 Provider
                provider_config = getattr(provider, "provider_config", None)
                provider_key = (
                    str(provider_config.get("id", "default"))
                    if isinstance(provider_config, dict)
                    else str(id(provider))
                )
                use_stream = stream_mode == "always" or (
                    stream_mode == "auto"
                    and provider_key in self._stream_only_providers
                )
                if not use_stream:
                    try:
                        response = await provider.text_chat(
                            prompt=prompt, system_prompt=system_prompt
                        )
                        completion_text = response.completion_text
                        record_llm_usage(
                            getattr(response, "usage", None),
                            prompt_text=prompt,
                            completion_text=completion_text or "",
                        )
                        return completion_text
                    except Exception as e:
                        # 仅当错误明确表示"不支持非流式"时才降级到流式
                        message = str(e).lower()
                        if stream_mode != "auto" or (
                            "non-stream" not in message and "non_stream" not in message
                        ):
                            raise
                        self._stream_only_providers.add(provider_key)
                        logger.warning(
                            "[MemoryProcessor] Provider 不支持非流式请求，"
                            f"本次自动改用流式调用: {e}"
                        )

                # 流式接口逐块返回 is_chunk=True 的增量，最后返回一次
                # is_chunk=False 的完整响应；只有完整响应才可作为结果。
                # 中断、超时或缺少完整响应时按失败处理，避免写入不完整记忆。
                completion_text = None
                final_usage = None
                async for chunk in provider.text_chat_stream(
                    prompt=prompt, system_prompt=system_prompt
                ):
                    if not getattr(chunk, "is_chunk", True) and chunk.completion_text:
                        completion_text = chunk.completion_text
                        final_usage = getattr(chunk, "usage", None)
                if not completion_text:
                    raise RuntimeError("流式调用未返回完整响应，已按失败处理")
                record_llm_usage(
                    final_usage,
                    prompt_text=prompt,
                    completion_text=completion_text,
                )
                return completion_text
            except Exception as e:
                last_error = e
                if not _is_transient_llm_error(e) or attempt == max_retries - 1:
                    raise
                wait_time = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[MemoryProcessor] LLM 调用遇到瞬时错误，{wait_time:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(wait_time)
        if last_error:
            raise last_error
        raise RuntimeError("LLM 调用失败，未捕获到具体异常")

    _try_fix_json = staticmethod(try_fix_json)

    async def process_conversation(
        self,
        messages: list[Message],
        is_group_chat: bool = False,
        persona_id: str | None = None,
    ) -> tuple[str, dict[str, Any], float]:
        """
        处理对话历史,生成结构化记忆

        Args:
            messages: 消息列表(Message对象)
            is_group_chat: 是否为群聊
            persona_id: 人格ID,用于获取人格提示词

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

        # 2. 选择合适的提示词模板（每次从 PromptManager 读取，确保 WebUI 保存后立即生效）
        # 使用 replace 而非 format，避免对话内容中的大括号导致解析错误
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        prompt = (
            self._get_chat_prompt(is_group_chat)
            .replace("{conversation}", conversation_text)
            .replace("{current_date}", current_date)
        )

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

            # 构建 system_prompt，嵌入人格提示
            system_prompt = await self._build_system_prompt_with_persona(persona_id)
            logger.debug(f"[MemoryProcessor] System Prompt: {system_prompt[:200]}...")

            llm_response_text = await self._call_llm_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
            )

            logger.info(
                f"[MemoryProcessor]  LLM 响应成功，响应长度={len(llm_response_text)}"
            )
            logger.debug(f"[MemoryProcessor] LLM 原始响应内容:\n{llm_response_text}")

            # 4. 解析LLM响应
            structured_data = parse_llm_response(llm_response_text, is_group_chat)

            # 4.5 质量校验
            quality = self._validate_summary_quality(structured_data)
            if quality == "low":
                logger.warning(
                    "[MemoryProcessor] 总结质量不达标（low），将标记但仍写入"
                )
            structured_data["_quality"] = quality

            # 5. 构建存储格式
            fallback_excerpt = (
                conversation_text[:200] + "..."
                if len(conversation_text) > 200
                else conversation_text
            )
            content, metadata = self._build_storage_format(
                fallback_excerpt, structured_data, is_group_chat
            )
            metadata["participant_identities"] = self._extract_participant_identities(
                messages
            )
            content = apply_source_time_tags(
                content,
                metadata,
                messages,
                self.config.get("include_source_time_tags", True),
            )
            # 将质量标记写入 metadata
            metadata["summary_quality"] = structured_data.get("_quality", "normal")

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
            # 不再降级处理，直接向上抛出异常，由调用方处理重试逻辑
            raise

    def _format_conversation(self, messages: list[Message]) -> str:
        """
        格式化对话历史为文本

        Args:
            messages: 消息列表(Message对象)

        Returns:
            格式化后的对话文本
        """
        return format_conversation(messages)

    @staticmethod
    def _extract_participant_identities(
        messages: list[Message],
    ) -> list[dict[str, Any]]:
        """Build stable graph identities from message sender IDs, not LLM names."""
        return extract_participant_identities(messages)

    def _build_storage_format(
        self,
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
        return build_storage_format(fallback_excerpt, structured_data, is_group_chat)

    @staticmethod
    def _validate_summary_quality(structured_data: dict[str, Any]) -> str:
        """校验总结质量，返回 "normal" 或 "low"。"""
        return validate_summary_quality(structured_data)

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
        normalized = normalize_parsed_data(structured_data, is_group_chat)
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
            validate_importance(normalized.get("importance")),
        )

    def classify_atoms_from_metadata(
        self,
        metadata: dict[str, Any],
        parent_importance: float = 0.5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[MemoryAtom]:
        """Generate time-aware memory atoms from key_facts in metadata.

        This is a post-processing step after process_conversation().
        It does NOT make additional LLM calls — classification is rule-based.
        """
        if not self.config.get("atom_enabled", True):
            return []
        key_facts: list[str] = metadata.get("key_facts", [])
        if not key_facts:
            return []
        topics = metadata.get("topics", [])
        participants = metadata.get("participants", [])
        atoms = classify_atoms(
            key_facts=key_facts,
            topics=topics,
            participants=participants,
            parent_importance=parent_importance,
            session_id=session_id,
            persona_id=persona_id,
        )
        metadata["atom_types"] = sorted({atom.atom_type.value for atom in atoms})
        return atoms

    async def merge_memories(self, memories: list[dict]) -> dict[str, Any]:
        """把一组零散记忆合并为一条精炼记忆（供记忆库整合使用）。

        Args:
            memories: 待合并的记忆列表，每条为 {"content": str, "metadata": dict}。

        Returns:
            包含 summary/key_facts/topics/importance 的字典。

        Raises:
            RuntimeError: LLM 不可用或解析失败时抛出。
        """
        items: list[dict[str, Any]] = []
        for i, mem in enumerate(memories, 1):
            metadata = mem.get("metadata") or {}
            summary = str(
                metadata.get("persona_summary") or str(mem.get("content", "")).strip()
            ).strip()
            items.append(
                {
                    "id": i,
                    "summary": summary,
                    "key_facts": metadata.get("key_facts") or [],
                    "topics": metadata.get("topics") or [],
                }
            )

        system_prompt = (
            "你是记忆整理助手。把多条关于同一主题或会话的零散记忆合并为一条精炼、"
            "信息无损的记忆摘要。保留所有关键事实与具体细节，去重并消除相互矛盾，"
            "避免泛化和丢失专有名词。只输出 JSON，不要输出任何其他内容。"
        )
        prompt = (
            f"以下是一组需要合并的记忆（共 {len(items)} 条）：\n"
            f"{json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
            "请将它们合并为一条记忆，按如下 JSON 格式输出：\n"
            '{"summary": "合并后的精炼摘要", "key_facts": ["事实1", "事实2"], '
            '"topics": ["主题1"], "importance": 0.5}'
        )

        text = await self._call_llm_with_retry(prompt, system_prompt)
        data = self._parse_merge_response(text)

        summary = str(data.get("summary", "")).strip()
        if not summary:
            raise RuntimeError("合并结果缺少 summary")

        return {
            "summary": summary,
            "key_facts": ensure_list(data.get("key_facts", []))[:5],
            "topics": ensure_list(data.get("topics", []))[:5],
            "importance": validate_importance(data.get("importance", 0.5)),
        }

    def _parse_merge_response(self, text: str) -> dict[str, Any]:
        """解析合并 LLM 响应中的 JSON，失败时抛出异常。"""
        candidates = [text]

        from ..utils import extract_json_from_response

        extracted = extract_json_from_response(text)
        if extracted != text.strip():
            candidates.append(extracted)

        last_error: Exception | None = None
        for candidate in candidates:
            for attempt in range(2):
                try:
                    data = json.loads(candidate)
                except (json.JSONDecodeError, TypeError) as e:
                    last_error = e
                    if attempt == 0:
                        candidate = self._try_fix_json(candidate)
                    continue
                if isinstance(data, dict):
                    return data
                last_error = TypeError("合并结果必须是 JSON 对象")
                break

        raise RuntimeError(f"合并结果 JSON 解析失败: {last_error}")
