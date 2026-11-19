"""Graph entry FTS must tokenize Chinese the same way it is queried (#450).

The entry FTS index used unicode61 over raw text while queries were jieba
tokenized, so a keyword present verbatim in the entry could not match. Entries
are now stored as CJK single characters plus adjacent bigrams, and long query
tokens are expanded to the same bigrams.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.models.graph_models import GraphEntry, GraphNode
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (
    GraphKeywordRetriever,
)
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

_KEY_CONTENT = "用户把备用钥匙交给小宁保管。"


async def _seed_documents(db_path: Path) -> None:
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, metadata TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO documents(id, metadata) VALUES (?, ?)",
            (1, json.dumps({"status": "active"})),
        )
        await db.commit()


async def _add_key_entry(store: GraphStore) -> None:
    node = GraphNode("topic", "备用钥匙", "备用钥匙")
    node_ids = await store.upsert_nodes([node])
    await store.add_entry(
        GraphEntry(
            entry_key="key-entry",
            source_memory_id=1,
            session_id="s",
            persona_id="p",
            entry_type="topic",
            content=_KEY_CONTENT,
            node_keys=[node.node_key],
        ),
        node_ids,
    )


@pytest.mark.asyncio
async def test_chinese_keyword_matches_entry_without_spaces(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_bigram.db"
    store = GraphStore(str(db_path))
    await store.initialize()
    await _seed_documents(db_path)
    await _add_key_entry(store)

    hits = await store.search_entries_by_bm25('"备用" OR "钥匙"', 10)

    assert [hit["source_memory_id"] for hit in hits] == [1]


@pytest.mark.asyncio
async def test_old_raw_fts_rows_are_backfilled_on_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_legacy_fts.db"
    store = GraphStore(str(db_path))
    await store.initialize()
    await _seed_documents(db_path)
    await _add_key_entry(store)

    # 模拟旧版本：入口 FTS 存未分词原文，且没有索引格式版本标记。
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("DELETE FROM livingmemory_graph_entries_fts")
        await db.execute(
            "INSERT INTO livingmemory_graph_entries_fts(entry_id, content) "
            "VALUES (?, ?)",
            (1, _KEY_CONTENT),
        )
        await db.execute("PRAGMA user_version = 0")
        await db.commit()

    reopened = GraphStore(str(db_path))
    await reopened.initialize()

    hits = await reopened.search_entries_by_bm25('"备用" OR "钥匙"', 10)
    assert [hit["source_memory_id"] for hit in hits] == [1]


@pytest.mark.asyncio
async def test_retriever_expands_long_tokens_to_bigrams(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_retriever_bigram.db"
    store = GraphStore(str(db_path))
    await store.initialize()
    await _seed_documents(db_path)
    await _add_key_entry(store)

    text_processor = AsyncMock()
    # jieba 常把长串切成整词；3 字以上需展开成二字组才能命中入口词元。
    text_processor.tokenize_async = AsyncMock(return_value=["备用钥匙"])
    retriever = GraphKeywordRetriever(store, text_processor)

    results = await retriever.search("备用钥匙", limit=5)

    assert [result.doc_id for result in results] == [1]
