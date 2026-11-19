"""Real plugin components for black-box tests.

Only the AstrBot host is stubbed (provider lookup, persona lookup, plugin data
directory); the engine, storage, retriever chain and tools are the real
implementations backed by a temporary SQLite database and a real FAISS index.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import asyncio
import json

from astrbot.api.provider import LLMResponse
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore

from tests.integration.test_real_db_end_to_end import (
    _DeterministicEmbeddingProvider,
    _DeterministicLLMProvider,
)

DEFAULT_CONFIG = {
    "atom_enabled": False,
    "graph_memory_enabled": False,
    "search_cache_enabled": False,
    "min_similarity_for_retrieval": 0.0,
    "min_importance_for_retrieval": 0.0,
    "recent_memory_count": 0,
}


class HostContext:
    """AstrBot ``Context`` stub covering the plugin's host-facing calls.

    Args:
        persona_id: Persona reported for every session, or None for "default".
        embedding_provider: Provider handed to ``get_all_embedding_providers``.
    """

    def __init__(
        self,
        *,
        persona_id: str | None = None,
        embedding_provider=None,
    ) -> None:
        self._persona_id = persona_id
        self._embedding_provider = embedding_provider
        self.platform_manager = SimpleNamespace(get_insts=lambda: [])
        self.persona_manager = SimpleNamespace(
            get_default_persona_v3=self._default_persona
        )
        self.conversation_manager = SimpleNamespace(
            get_curr_conversation_id=_none,
            get_conversation=_none,
        )
        self.web_apis: dict[str, tuple] = {}
        self.llm_tools: list = []

    async def _default_persona(self, umo=None):
        return {"name": self._persona_id} if self._persona_id else None

    def get_config(self) -> dict:
        return {"platform": []}

    def register_web_api(self, path, handler, methods, description="") -> None:
        self.web_apis[path] = (handler, methods)

    def add_llm_tools(self, *tools) -> None:
        self.llm_tools.extend(tools)

    def get_provider_by_id(self, provider_id):
        return None

    def get_all_embedding_providers(self) -> list:
        return [self._embedding_provider] if self._embedding_provider else []

    def get_all_providers(self) -> list:
        return []


async def _none(*_args, **_kwargs):
    return None


async def make_engine(
    tmp_path: Path,
    *,
    config: dict | None = None,
    embedding_provider=None,
) -> tuple[MemoryEngine, FaissVecDB]:
    """Build a real engine on a temporary SQLite database and FAISS index."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    provider = embedding_provider or _DeterministicEmbeddingProvider()
    # 生产环境中 FAISS 文档库与引擎主库是同一个文件；这里保持一致，
    # 否则访问统计会写到另一份 documents 表里。
    db_path = str(tmp_path / "livingmemory.db")
    db = FaissVecDB(db_path, str(tmp_path / "livingmemory.index"), provider)
    await db.initialize()
    engine = MemoryEngine(
        db_path=db_path,
        faiss_db=db,
        config={**DEFAULT_CONFIG, **(config or {})},
    )
    await engine.initialize()
    return engine, db


async def close_engine(engine: MemoryEngine, db: FaissVecDB) -> None:
    await engine.close()
    await db.close()


def merge_config(base: dict, overrides: dict) -> dict:
    """Recursively merge nested config dicts (shallow merge loses sub-keys)."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


EVENT_HANDLER_CONFIG = {
    "recall_engine": {
        "top_k": 3,
        "injection_method": "extra_user_content",
        "min_similarity_for_retrieval": 0.0,
        "recent_memory_count": 0,
    },
    "reflection_engine": {
        "summary_trigger_rounds": 1,
        "source_retention_importance_threshold": 0.8,
    },
    "session_manager": {"max_messages_per_session": 100},
    "filtering_settings": {"use_persona_filtering": False},
}


async def make_handler(
    tmp_path: Path,
    *,
    config: dict | None = None,
    llm_provider=None,
    host=None,
):
    """Build a real EventHandler with a real engine and conversation store.

    Args:
        tmp_path: Directory for the SQLite databases.
        config: Overrides merged into ``EVENT_HANDLER_CONFIG``.
        llm_provider: Summarization provider double (external service boundary).
        host: Host context stub; defaults to one with no persona.

    Returns:
        ``(handler, manager, store, engine, faiss_db)``; call
        ``close_handler`` when done.
    """
    merged = merge_config(EVENT_HANDLER_CONFIG, config or {})
    profile = merged.get("profile_settings", {})
    recall = merged.get("recall_engine", {})
    engine, db = await make_engine(
        tmp_path / "engine",
        config={
            # 检索阈值是引擎配置，必须与 handler 配置保持一致
            "min_similarity_for_retrieval": recall.get(
                "min_similarity_for_retrieval", 0.0
            ),
            "min_importance_for_retrieval": recall.get(
                "min_importance_for_retrieval", 0.0
            ),
            "recent_memory_count": recall.get("recent_memory_count", 0),
            "top_k": recall.get("top_k", 5),
            "profile_enabled": profile.get("enabled", True),
            "profile_max_chars": profile.get("max_chars", 300),
            "profile_min_confidence": profile.get("min_confidence", 0.6),
        },
    )
    store = ConversationStore(str(tmp_path / "conversations.db"))
    await store.initialize()
    manager = ConversationManager(
        store=store, max_cache_size=10, context_window_size=50, session_ttl=3600
    )
    handler = EventHandler(
        context=host or HostContext(),
        config_manager=ConfigManager(merged),
        memory_engine=engine,
        memory_processor=MemoryProcessor(
            config=merged,
            llm_provider=llm_provider or _DeterministicLLMProvider(),
            context=host or HostContext(),
        ),
        conversation_manager=manager,
    )
    return handler, manager, store, engine, db


async def close_handler(handler, manager, store, engine, db) -> None:
    """Tear down a bundle returned by :func:`make_handler`."""
    await handler.shutdown()
    await store.close()
    await close_engine(engine, db)


class SummaryProvider:
    """Summarization provider double that records concurrency and can gate/fail.

    Args:
        importance: Importance reported in the summary payload.
        gate: Optional event every call waits on before answering.
        error: Optional exception raised instead of answering.
        delay: Seconds to hold each call (for concurrency assertions).
    """

    def __init__(
        self,
        *,
        importance: float = 0.82,
        gate=None,
        error=None,
        delay=0.0,
        vary: bool = False,
    ):
        self.importance = importance
        self.vary = vary
        self.gate = gate
        self.error = error
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def text_chat(self, prompt=None, **_kwargs) -> LLMResponse:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            # vary=True 时每次返回语义与用词都不同的摘要，避免近重复合并
            # 掩盖“分段消费”这类行为
            variants = (
                "用户计划周末去山里徒步并准备了装备。",
                "用户提到最近在读科幻小说并推荐了两本。",
                "用户在讨论学吉他，想先从指法练习开始。",
                "用户说公司换了新的项目管理流程。",
                "用户分享了一道番茄炖牛腩的做法。",
                "用户在考虑养一只橘猫并比较猫砂种类。",
            )
            summary = (
                variants[(self.calls - 1) % len(variants)]
                if self.vary
                else "用户在讨论跑步习惯与周末安排。"
            )
            payload = {
                "summary": summary,
                "topics": ["health"],
                "key_facts": ["用户开始跑步"],
                "sentiment": "positive",
                "importance": self.importance,
            }
            return LLMResponse(
                role="assistant",
                completion_text=json.dumps(payload),
            )
        finally:
            self.active -= 1


async def make_handler_bundle(tmp_path: Path, *, memory_engine=None, config=None):
    """Real handler + real conversation store; engine may be substituted.

    Returns ``(handler, manager, store)`` for tests that only assert on
    conversation state. When ``memory_engine`` is omitted a real engine is used.

    Args:
        tmp_path: Directory for the databases.
        memory_engine: Optional engine substitute (external collaborator double).
        config: Optional config overrides.

    Returns:
        ``(handler, manager, store)``.
    """
    handler, manager, store, engine, db = await make_handler(
        tmp_path, config=config, host=HostContext()
    )
    if memory_engine is not None:
        handler.memory_engine = memory_engine
        handler._memory_reflection.memory_engine = memory_engine
        handler._memory_recall.memory_engine = memory_engine
    return handler, manager, store


async def seed_turns(manager, session_id: str, checkpoints) -> None:
    """Seed complete user/assistant turns carrying a checkpoint id each."""
    import time

    base = time.time() - 120
    for index, checkpoint in enumerate(checkpoints):
        await manager.add_message(
            session_id=session_id,
            role="user",
            content=f"q-{checkpoint}",
            sender_id="user-1",
            metadata={"llm_checkpoint_id": checkpoint},
            timestamp=base + index * 2,
        )
        await manager.add_message(
            session_id=session_id,
            role="assistant",
            content=f"a-{checkpoint}",
            sender_id="bot-1",
            metadata={"llm_checkpoint_id": checkpoint},
            timestamp=base + index * 2 + 1,
        )
