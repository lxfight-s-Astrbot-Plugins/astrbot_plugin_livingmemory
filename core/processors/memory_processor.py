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
from ..base.exceptions import LowQualityMemoryError
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

# 进程内低质量总结拒绝计数（不落盘；用于观察 LLM 输出质量）
_low_quality_rejections = 0


def get_low_quality_rejection_count() -> int:
    """返回当前进程拒绝写入的低质量总结累计次数。"""
    return _low_quality_rejections


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
    参数错误等不属于瞬时错误（鉴权失败由 Provider 回退链处理，不重试）。
    重试只发生在同一个 Provider 内，避免同一错误反复消耗 LLM 调用额度。
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


# 与配置默认值保持一致：单个 Provider 单次调用默认超时（秒）。
DEFAULT_LLM_CALL_TIMEOUT_SECONDS = 60

# 鉴权/权限失败标记：这类错误换一个 Provider 才有意义，不应重试或直接放弃。
_AUTH_ERROR_MARKERS = (
    "unauthorized",
    "unauthorised",
    "authentication",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "permission denied",
    "forbidden",
    "access denied",
    "http 401",
    "http 403",
    "status code 401",
    "status code 403",
)


def _is_auth_llm_error(exc: BaseException) -> bool:
    """判断异常是否为 Provider 鉴权/权限错误。"""
    if _extract_http_status(exc) in (401, 403):
        return True
    type_names = {cls.__name__.lower() for cls in type(exc).__mro__}
    if any(
        "authentication" in name or "permissiondenied" in name for name in type_names
    ):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _AUTH_ERROR_MARKERS)


def _is_provider_unavailable_error(exc: BaseException) -> bool:
    """判断异常是否应切换到下一个 Provider。

    超时、HTTP 429/5xx、连接类错误与鉴权失败属于 Provider 级不可用；
    解析/校验/请求格式等错误换一个模型也无济于事，直接抛出。
    """
    return _is_transient_llm_error(exc) or _is_auth_llm_error(exc)


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
        # 主 Provider 不可用时按顺序尝试的备用 Provider ID（去重、保序）。
        self._fallback_provider_ids = self._normalize_provider_ids(
            self.config.get("llm_fallback_provider_ids")
        )
        # 每个 Provider 单次调用的超时（秒），由 asyncio.wait_for 生效。
        self._llm_call_timeout_seconds = self._normalize_call_timeout(
            self.config.get("llm_call_timeout_seconds")
        )

        # 加载提示词模板
        self._load_prompts()

    @staticmethod
    def _normalize_provider_ids(value: Any) -> list[str]:
        """Normalize fallback provider IDs into a deduplicated ordered list."""
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        provider_ids: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            provider_id = item.strip()
            if provider_id and provider_id not in seen:
                seen.add(provider_id)
                provider_ids.append(provider_id)
        return provider_ids

    @staticmethod
    def _normalize_call_timeout(value: Any) -> float:
        """Normalize the per-call timeout; invalid values fall back to default."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return float(DEFAULT_LLM_CALL_TIMEOUT_SECONDS)
        timeout = float(value)
        if timeout <= 0:
            return float(DEFAULT_LLM_CALL_TIMEOUT_SECONDS)
        return timeout

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

    @staticmethod
    def _provider_label(provider: Any, configured_id: str = "") -> str:
        """Return the provider id used in logs and usage statistics."""
        if configured_id:
            return configured_id
        provider_config = getattr(provider, "provider_config", None)
        if isinstance(provider_config, dict):
            provider_id = provider_config.get("id")
        else:
            provider_id = getattr(provider_config, "id", None)
        return str(provider_id) if provider_id else "default"

    def _resolve_fallback_provider(self, provider_id: str):
        """Resolve one fallback provider by ID; return None when unavailable."""
        if not self.context:
            logger.warning(
                f"[MemoryProcessor] No context to resolve fallback LLM provider "
                f"'{provider_id}', skipped"
            )
            return None
        try:
            provider = self.context.get_provider_by_id(provider_id)
        except Exception as e:
            logger.warning(
                f"[MemoryProcessor] Failed to resolve fallback LLM provider "
                f"'{provider_id}': {e}"
            )
            return None
        if provider is None or not hasattr(provider, "text_chat"):
            logger.warning(
                f"[MemoryProcessor] Fallback LLM provider '{provider_id}' is "
                f"missing or not a chat provider, skipped"
            )
            return None
        return provider

    async def _resolve_llm_provider_chain(self) -> list[tuple[str, Any]]:
        """Resolve providers in order: primary first, then configured fallbacks.

        Unresolvable fallback IDs are logged and skipped; the same provider is
        never added twice. An empty chain means no provider is available.
        """
        chain: list[tuple[str, Any]] = []
        seen_ids: set[str] = set()
        seen_objects: set[int] = set()

        primary = await self._get_current_llm_provider()
        if primary is not None:
            configured_id = (
                self._llm_provider if isinstance(self._llm_provider, str) else ""
            )
            label = self._provider_label(primary, configured_id)
            chain.append((label, primary))
            seen_ids.add(label)
            seen_objects.add(id(primary))
        elif self._fallback_provider_ids:
            logger.warning(
                "[MemoryProcessor] Primary LLM provider is unavailable, trying "
                f"fallback providers: {self._fallback_provider_ids}"
            )

        for provider_id in self._fallback_provider_ids:
            if provider_id in seen_ids:
                continue
            provider = self._resolve_fallback_provider(provider_id)
            if provider is None:
                continue
            label = self._provider_label(provider, provider_id)
            if label in seen_ids or id(provider) in seen_objects:
                logger.warning(
                    f"[MemoryProcessor] Fallback LLM provider '{provider_id}' "
                    f"duplicates an earlier provider, skipped"
                )
                continue
            chain.append((label, provider))
            seen_ids.add(label)
            seen_objects.add(id(provider))

        return chain

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
        """Call the LLM with provider fallback, per-provider retry and timeout.

        Providers are tried in order: the configured/primary provider first,
        then ``llm_fallback_provider_ids``. Inside one provider only transient
        errors (timeout, HTTP 429/5xx, connection errors) are retried once;
        authentication failures or exhausted retries switch to the next
        provider. Parsing/validation/request-format errors are raised directly
        because another model would fail the same way.

        Streaming is selected by ``llm_stream_mode`` (issue #265): ``never``
        always uses non-streaming, ``always`` always streams, ``auto`` prefers
        non-streaming and remembers stream-only providers per provider id.

        Args:
            prompt: Prompt text.
            system_prompt: System prompt text.
            max_retries: Maximum attempts inside one provider (first call
                included), default 2.

        Returns:
            LLM response text.

        Raises:
            RuntimeError: No provider is available, or the streaming response
                is incomplete.
            Exception: The last provider error when every provider fails.
        """
        # Unknown/missing values fall back to auto.
        stream_mode = self.config.get("llm_stream_mode", "auto")
        if stream_mode not in ("auto", "always", "never"):
            stream_mode = "auto"

        chain = await self._resolve_llm_provider_chain()
        if not chain:
            raise RuntimeError("LLM Provider 不可用")

        tried: list[str] = []
        last_error: BaseException | None = None
        for index, (provider_id, provider) in enumerate(chain):
            fallback_used = index > 0
            try:
                return await self._call_provider_with_retry(
                    provider=provider,
                    provider_id=provider_id,
                    fallback_used=fallback_used,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    stream_mode=stream_mode,
                    max_retries=max_retries,
                )
            except Exception as exc:
                if not _is_provider_unavailable_error(exc):
                    # Parse/validation/request-format errors are model-agnostic.
                    raise
                last_error = exc
                tried.append(f"{provider_id} ({type(exc).__name__})")
                if index + 1 < len(chain):
                    logger.warning(
                        f"[MemoryProcessor] LLM provider '{provider_id}' failed "
                        f"({type(exc).__name__}: {exc}); falling back to "
                        f"'{chain[index + 1][0]}' (tried: {', '.join(tried)})"
                    )
                else:
                    logger.error(
                        f"[MemoryProcessor] All LLM providers failed; last error "
                        f"from '{provider_id}' ({type(exc).__name__}: {exc}); "
                        f"tried: {', '.join(tried)}"
                    )

        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM Provider 不可用")

    async def _call_provider_with_retry(
        self,
        *,
        provider: Any,
        provider_id: str,
        fallback_used: bool,
        prompt: str,
        system_prompt: str,
        stream_mode: str,
        max_retries: int,
    ) -> str:
        """Call one provider with the existing transient-error retry policy."""
        provider_config = getattr(provider, "provider_config", None)
        provider_key = (
            str(provider_config.get("id", "default"))
            if isinstance(provider_config, dict)
            else str(id(provider))
        )

        last_error: BaseException | None = None
        for attempt in range(max_retries):
            try:
                use_stream = stream_mode == "always" or (
                    stream_mode == "auto"
                    and provider_key in self._stream_only_providers
                )
                if not use_stream:
                    try:
                        response = await asyncio.wait_for(
                            provider.text_chat(
                                prompt=prompt, system_prompt=system_prompt
                            ),
                            timeout=self._llm_call_timeout_seconds,
                        )
                        completion_text = response.completion_text
                        record_llm_usage(
                            getattr(response, "usage", None),
                            prompt_text=prompt,
                            completion_text=completion_text or "",
                            provider_id=provider_id,
                            fallback_used=fallback_used,
                        )
                        return completion_text
                    except Exception as e:
                        # Only a clear "non-stream unsupported" error downgrades
                        # to streaming; other errors keep the normal retry path.
                        message = str(e).lower()
                        if stream_mode != "auto" or (
                            "non-stream" not in message and "non_stream" not in message
                        ):
                            raise
                        self._stream_only_providers.add(provider_key)
                        logger.warning(
                            "[MemoryProcessor] Provider does not support "
                            f"non-stream requests, switching to streaming: {e}"
                        )

                # Streaming: only a complete response (is_chunk=False) counts.
                completion_text, final_usage = await asyncio.wait_for(
                    self._collect_stream_response(provider, prompt, system_prompt),
                    timeout=self._llm_call_timeout_seconds,
                )
                if not completion_text:
                    raise RuntimeError("流式调用未返回完整响应，已按失败处理")
                record_llm_usage(
                    final_usage,
                    prompt_text=prompt,
                    completion_text=completion_text,
                    provider_id=provider_id,
                    fallback_used=fallback_used,
                )
                return completion_text
            except Exception as e:
                last_error = e
                if not _is_transient_llm_error(e) or attempt == max_retries - 1:
                    raise
                wait_time = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[MemoryProcessor] LLM call hit a transient error, retrying "
                    f"in {wait_time:.1f}s ({attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(wait_time)
        if last_error:
            raise last_error
        raise RuntimeError("LLM 调用失败，未捕获到具体异常")

    @staticmethod
    async def _collect_stream_response(
        provider: Any, prompt: str, system_prompt: str
    ) -> tuple[str | None, Any]:
        """Consume a streaming response and return complete text plus usage."""
        completion_text = None
        final_usage = None
        async for chunk in provider.text_chat_stream(
            prompt=prompt, system_prompt=system_prompt
        ):
            if not getattr(chunk, "is_chunk", True) and chunk.completion_text:
                completion_text = chunk.completion_text
                final_usage = getattr(chunk, "usage", None)
        return completion_text, final_usage

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
            ValueError: 消息列表为空。
            MemoryParseError: LLM 响应无法解析为结构化记忆（调用方应重试，不落库）。
            LowQualityMemoryError: 总结质量不达标（调用方应重试，不落库）。
            Exception: 其他 LLM 调用或处理失败。
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

            # 4.5 质量校验：低质量总结默认不写入主库，由调用方按可重试失败处理
            quality = self._validate_summary_quality(structured_data)
            if quality == "low":
                global _low_quality_rejections
                _low_quality_rejections += 1
                logger.warning(
                    "[MemoryProcessor] 总结质量不达标（low），拒绝写入主库: "
                    f"summary={(structured_data.get('summary') or '')[:60]!r}, "
                    f"key_facts={len(structured_data.get('key_facts') or [])}, "
                    f"rejected_total={_low_quality_rejections}"
                )
                raise LowQualityMemoryError(
                    "总结质量不达标（summary/key_facts/importance 校验失败）"
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
