"""
消息处理工具模块
负责消息内容提取、去重、限制等操作
"""

import asyncio
import hashlib
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image as PillowImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

if TYPE_CHECKING:
    from astrbot.core.message.components import Image

    from ..base.config_manager import ConfigManager
    from ..managers.conversation_manager import ConversationManager


_STICKER_SUMMARIES = {"动画表情", "[动画表情]", "[表情]"}


def _is_animated_local_gif(image: "Image") -> bool:
    """Inspect the original local attachment without downloading or inferring its type."""
    ref = getattr(image, "path", None) or image.url or image.file
    if not isinstance(ref, str) or not ref:
        return False
    try:
        if ref.startswith("file:"):
            from astrbot.core.utils.media_utils import file_uri_to_path

            ref = file_uri_to_path(ref)
        path = Path(ref)
        if (
            ref.startswith(("//", "\\\\"))
            or not path.is_absolute()
            or not path.is_file()
        ):
            return False
        with path.open("rb") as stream:
            if stream.read(6) not in (b"GIF87a", b"GIF89a"):
                return False
            stream.seek(0)
            with PillowImage.open(stream, formats=("GIF",)) as picture:
                return bool(getattr(picture, "is_animated", False))
    except (
        EOFError,
        ImportError,
        OSError,
        ValueError,
        PillowImage.DecompressionBombError,
    ):
        return False


class MessageUtils:
    """消息处理工具类"""

    def __init__(
        self,
        config_manager: "ConfigManager",
        conversation_manager: "ConversationManager | None",
    ):
        """
        初始化消息处理工具

        Args:
            config_manager: 配置管理器
            conversation_manager: 会话管理器
        """
        self.config_manager = config_manager
        self.conversation_manager = conversation_manager

        # 消息去重缓存
        self._message_dedup_cache: dict[str, float] = {}
        self._dedup_cache_max_size = 1000
        self._dedup_cache_ttl = 300

    async def build_dedup_key(
        self, event: AstrMessageEvent, session_id: str, content: str
    ) -> str | None:
        """构建去重键：优先使用 message_id，缺失时退化为消息内容指纹。"""
        raw_message_id = getattr(
            getattr(event, "message_obj", None), "message_id", None
        )
        if raw_message_id is not None:
            message_id = str(raw_message_id).strip()
            if message_id:
                return f"id:{message_id}"

        sender_id = event.get_sender_id() if hasattr(event, "get_sender_id") else ""
        timestamp = getattr(getattr(event, "message_obj", None), "timestamp", 0)
        fingerprint = f"{session_id}|{sender_id}|{timestamp}|{content}"
        digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
        return f"fallback:{digest}"

    async def is_duplicate_message(self, dedup_key: str | None) -> bool:
        """检查消息是否已经处理过（惰性过期 + 溢出时逐条淘汰）"""
        if not dedup_key:
            return False

        result = dedup_key in self._message_dedup_cache
        if not result:
            return False

        # 惰性过期检查：命中时若已过期则视为未命中
        if time.time() - self._message_dedup_cache[dedup_key] > self._dedup_cache_ttl:
            del self._message_dedup_cache[dedup_key]
            return False

        return True

    async def mark_message_processed(self, dedup_key: str | None):
        """标记消息已处理（超限时淘汰最早插入的条目）"""
        if not dedup_key:
            return
        cache = self._message_dedup_cache
        if len(cache) >= self._dedup_cache_max_size:
            # 淘汰最早插入的条目（O(n) 但仅超限时触发，n≤1000）
            oldest_key = min(cache.items(), key=lambda x: x[1])[0]
            del cache[oldest_key]
        cache[dedup_key] = time.time()

    @staticmethod
    def get_request_image_captions(
        req: ProviderRequest, *, dedicated_parts_only: bool = False
    ) -> list[str]:
        """Read AstrBot image captions from the current request."""
        captions = []
        for part in getattr(req, "extra_user_content_parts", None) or ():
            text = (
                part.get("text")
                if isinstance(part, dict)
                else getattr(part, "text", None)
            )
            if not isinstance(text, str):
                continue
            pattern = r"<image_caption>(.*?)</image_caption>"
            if dedicated_parts_only and not re.fullmatch(
                pattern, text.strip(), re.DOTALL
            ):
                continue
            captions.extend(
                caption.strip()
                for caption in re.findall(pattern, text, re.DOTALL)
                if caption.strip()
            )
        return captions

    @staticmethod
    def get_request_image_urls(
        req: ProviderRequest, *, include_image_urls: bool = True
    ) -> list[str]:
        """Read current image attachments without changing the provider request."""
        urls = (
            list(getattr(req, "image_urls", None) or ()) if include_image_urls else []
        )
        for part in getattr(req, "extra_user_content_parts", None) or ():
            if isinstance(part, dict):
                if part.get("type") == "image_url" and not part.get("_no_save"):
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict):
                        urls.append(image_url.get("url"))
            elif getattr(part, "type", None) == "image_url" and not getattr(
                part, "_no_save", False
            ):
                urls.append(getattr(getattr(part, "image_url", None), "url", None))
        return list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))

    @staticmethod
    async def get_sticker_images(
        event: AstrMessageEvent, images: list["Image"]
    ) -> set[int]:
        """Identify current QQ stickers and animated GIF reactions."""
        get_platform_name = getattr(event, "get_platform_name", None)
        if (
            not images
            or not callable(get_platform_name)
            or get_platform_name() != "aiocqhttp"
        ):
            return set()

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        segments = (
            raw_message.get("message") if isinstance(raw_message, Mapping) else None
        )
        raw_images = (
            [
                segment.get("data")
                for segment in segments
                if isinstance(segment, Mapping) and segment.get("type") == "image"
            ]
            if isinstance(segments, list)
            else []
        )

        stickers = set()
        # Trust raw positions only when they still align with normalized images.
        if len(raw_images) == len(images):
            for index, data in enumerate(raw_images):
                if not isinstance(data, Mapping):
                    continue
                if (
                    str(data.get("sub_type", "")).strip() == "1"
                    or data.get("emoji_id")
                    or data.get("emoji_package_id")
                    or str(data.get("summary", "")).strip() in _STICKER_SUMMARIES
                ):
                    stickers.add(index)

        if len(stickers) < len(images):

            def animated_reactions() -> set[int]:
                return {
                    index
                    for index, image in enumerate(images)
                    if index not in stickers and _is_animated_local_gif(image)
                }

            stickers.update(await asyncio.to_thread(animated_reactions))
        return stickers

    async def extract_message_content(
        self, event: AstrMessageEvent, req: ProviderRequest | None = None
    ) -> str:
        """提取消息内容，按组件原始顺序拼接，保留文字与图片的相对位置。
        若 AstrBot 已完成图片转述（req.extra_user_content_parts 中含 <image_caption> 标签），
        则按图片出现顺序依次替换，不会重复消费同一条转述。
        """
        from astrbot.core.message.components import (
            At,
            AtAll,
            Face,
            File,
            Forward,
            Image,
            Plain,
            Record,
            Reply,
            Video,
        )

        caption_queue = self.get_request_image_captions(req) if req is not None else []

        parts = []
        caption_idx = 0

        # 按组件原始顺序遍历，保留文字与图片的相对位置
        for component in event.get_messages():
            if isinstance(component, Plain):
                text = component.text.strip() if component.text else ""
                if text:
                    parts.append(text)
            elif isinstance(component, Image):
                if caption_idx < len(caption_queue):
                    parts.append(f"[图片: {caption_queue[caption_idx]}]")
                    caption_idx += 1
                else:
                    parts.append("[图片]")
            elif isinstance(component, Record):
                parts.append("[语音]")
            elif isinstance(component, Video):
                parts.append("[视频]")
            elif isinstance(component, File):
                file_name = component.name or "未知文件"
                parts.append(f"[文件: {file_name}]")
            elif isinstance(component, Face):
                parts.append(f"[表情:{component.id}]")
            elif isinstance(component, At):
                if isinstance(component, AtAll):
                    parts.append("[At:全体成员]")
                else:
                    parts.append(f"[At:{component.qq}]")
            elif isinstance(component, Forward):
                parts.append("[转发消息]")
            elif isinstance(component, Reply):
                if component.message_str:
                    parts.append(f"[引用: {component.message_str[:30]}]")
                else:
                    parts.append("[引用消息]")
            else:
                component_type = getattr(
                    component,
                    "type",
                    component.__class__.__name__,
                )
                logger.debug(f"跳过未知消息组件: {component_type}")

        return " ".join(parts).strip()

    async def get_event_message_str(self, event: AstrMessageEvent) -> str:
        """Get normalized raw message text from event."""
        get_message_str = getattr(event, "get_message_str", None)
        raw_message = ""

        if callable(get_message_str):
            raw_message = get_message_str()
            if asyncio.iscoroutine(raw_message):
                raw_message = await raw_message
        else:
            raw_message = getattr(event, "message_str", "")

        if not isinstance(raw_message, str):
            return ""

        return raw_message.strip()

    async def enforce_message_limit(self, session_id: str):
        """Enforce the configured history cap, preferring summarized messages."""
        if not self.conversation_manager:
            return

        max_messages = self.config_manager.get(
            "session_manager.max_messages_per_session", 1000
        )
        # Consume the one-shot count captured by the latest insertion transaction;
        # when absent (e.g. direct store writes or concurrent consumer) or above
        # the cap, fall back to a fresh DB count check.
        pop_count = getattr(self.conversation_manager, "pop_recent_message_count", None)
        recent_count = pop_count(session_id) if callable(pop_count) else None
        if (
            isinstance(recent_count, int)
            and not isinstance(recent_count, bool)
            and recent_count <= max_messages
        ):
            return
        cleanup_batch_size = self.config_manager.get(
            "session_manager.cleanup_batch_size", 50
        )
        try:
            cleanup_batch_size = int(cleanup_batch_size)
        except (TypeError, ValueError):
            cleanup_batch_size = 50
        cleanup_batch_size = max(1, cleanup_batch_size)

        if (
            not self.conversation_manager.store
            or not self.conversation_manager.store.connection
        ):
            return

        try:
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )

            if actual_count <= max_messages:
                return

            # 获取已总结的消息位置
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )

            # 计算需要删除的数量；超过上限时按批量清理，减少每轮只删 1 条的抖动。
            overflow_count = actual_count - max_messages
            target_delete = max(overflow_count, cleanup_batch_size)

            # Keep the configured hard cap even when no summary has committed.
            safe_to_delete = max(
                overflow_count, min(target_delete, last_summarized_index)
            )
            delete_unsummarized = safe_to_delete > last_summarized_index

            if delete_unsummarized:
                logger.warning(
                    f"[{session_id}] History cap requires dropping "
                    f"{safe_to_delete - last_summarized_index} unsummarized oldest messages"
                )
            if delete_unsummarized:
                actually_deleted = (
                    await self.conversation_manager.trim_session_messages(
                        session_id, safe_to_delete, delete_unsummarized=True
                    )
                )
            else:
                actually_deleted = (
                    await self.conversation_manager.trim_session_messages(
                        session_id, safe_to_delete
                    )
                )

            new_actual_count = max(0, actual_count - actually_deleted)
            # 清除缓存（使用公共接口）
            await self.conversation_manager.invalidate_cache(session_id)

            logger.info(
                f"[{session_id}] History trimmed: deleted={actually_deleted}, "
                f"retained={new_actual_count}, previous_summary_cursor={last_summarized_index}"
            )

        except Exception as e:
            logger.error(f"[{session_id}] 删除旧消息失败: {e}", exc_info=True)
