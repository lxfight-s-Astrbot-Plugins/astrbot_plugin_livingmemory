"""中文短查询/实体匹配：字符二元组补齐 jieba 切分差异。"""

from __future__ import annotations

import json

import pytest

from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import (
    BM25Retriever,
    with_cjk_bigrams,
)


def test_bigrams_cover_cjk_runs():
    merged = with_cjk_bigrams(["杭州", "咖啡"], "杭州喝咖啡 Project Alpha")

    assert "杭州" in merged and "咖啡" in merged
    assert "州喝" in merged and "喝咖" in merged


@pytest.mark.asyncio
async def test_short_entity_query_matches_by_bigram(tmp_path):
    retriever = BM25Retriever(str(tmp_path / "bm25.db"), TextProcessor())
    await retriever.initialize()
    text = "我喜欢在耶加雪菲产区的豆子里挑浅烘焙"
    async with retriever._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY,"
            " doc_id VARCHAR UNIQUE, text VARCHAR, metadata TEXT)"
        )
        await db.execute(
            "INSERT INTO documents (id, doc_id, text, metadata) VALUES (1, 'd1', ?, ?)",
            (text, json.dumps({"status": "active"}, ensure_ascii=False)),
        )
        await db.commit()
    await retriever.add_document(1, text)

    results = await retriever.search("雪菲")

    assert [result.doc_id for result in results] == [1]
