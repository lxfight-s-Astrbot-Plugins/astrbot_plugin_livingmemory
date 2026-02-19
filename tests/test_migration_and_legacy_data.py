"""
测试旧数据迁移（v3 -> v4）及迁移后的处理效果。

覆盖场景：
私聊记忆（private_chat）和群聊记忆（group_chat）各自的迁移与注入行为，
以及各种边界情况（NULL metadata、空字符串、极长内容、混合新旧数据等）。
"""

import json
import time

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.storage.db_migration import DBMigration
from astrbot_plugin_livingmemory.core.utils import format_memories_for_injection


# ---------------------------------------------------------------------------
# 真实记忆内容样本（私聊 / 群聊，长文本）
# ---------------------------------------------------------------------------

PRIVATE_MEMORY_LONG = (
    "用户在私聊中详细描述了自己的工作情况：目前在一家互联网公司担任后端工程师，"
    "主要使用 Python 和 Go 开发微服务，团队规模约 20 人。"
    "用户提到最近在学习 Rust，希望将来能用于高性能场景。"
    "此外用户表示对工作压力较大，每周工作超过 60 小时，希望能找到更好的工作生活平衡。"
    "用户的家庭情况：已婚，有一个两岁的孩子，住在上海浦东新区。"
    "用户的兴趣爱好包括：打羽毛球、看科幻小说、偶尔做饭。"
)

GROUP_MEMORY_LONG = (
    "群聊中多名成员讨论了关于 AI 工具在日常工作中的应用。"
    "张三表示自己每天使用 ChatGPT 辅助写代码，效率提升了约 30%。"
    "李四则认为 AI 生成的代码质量参差不齐，需要仔细审查，不能盲目信任。"
    "王五分享了一个使用 Claude 进行文档总结的实际案例，节省了大量时间。"
    "群内普遍认为 AI 工具是辅助而非替代，关键还是要理解业务逻辑。"
    "讨论中还提到了数据安全问题：不应将公司敏感代码直接粘贴到公共 AI 服务中。"
    "最终群成员达成共识：建议公司内部部署私有化 LLM 方案。"
)

PRIVATE_METADATA_V1 = {
    "importance": 0.85,
    "topics": ["工作", "技术学习", "生活压力"],
    "key_facts": [
        "后端工程师，使用 Python 和 Go",
        "正在学习 Rust",
        "每周工作超过 60 小时",
        "住在上海浦东，已婚有孩",
    ],
    "sentiment": "mixed",
    "interaction_type": "private_chat",
    "session_id": "aiocqhttp:FriendMessage:10001",
    "persona_id": "default",
}

GROUP_METADATA_V1 = {
    "importance": 0.78,
    "topics": ["AI工具", "工作效率", "数据安全"],
    "key_facts": [
        "张三使用 ChatGPT 效率提升 30%",
        "李四认为 AI 代码需仔细审查",
        "建议公司内部部署私有化 LLM",
    ],
    "participants": ["张三", "李四", "王五"],
    "sentiment": "positive",
    "interaction_type": "group_chat",
    "session_id": "aiocqhttp:GroupMessage:88888",
    "persona_id": "default",
}

PRIVATE_METADATA_V2 = {
    **PRIVATE_METADATA_V1,
    "canonical_summary": "用户是后端工程师，使用 Python/Go，正学 Rust，工作压力大，住上海浦东",
    "persona_summary": "你是个很努力的工程师呢，还在学 Rust，真厉害！",
    "summary_schema_version": "v2",
    "summary_quality": "normal",
    "source_window": {
        "session_id": "aiocqhttp:FriendMessage:10001",
        "start_index": 0,
        "end_index": 8,
        "message_count": 9,
    },
}

GROUP_METADATA_V2 = {
    **GROUP_METADATA_V1,
    "canonical_summary": "群聊讨论 AI 工具应用，建议内部部署私有化 LLM，注意数据安全",
    "persona_summary": "大家对 AI 工具的看法挺理性的，既肯定价值也注意风险。",
    "summary_schema_version": "v2",
    "summary_quality": "normal",
    "source_window": {
        "session_id": "aiocqhttp:GroupMessage:88888",
        "start_index": 10,
        "end_index": 25,
        "message_count": 16,
    },
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

async def _create_legacy_db(db_path: str, rows: list[dict]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT
            )
        """)
        for row in rows:
            await db.execute(
                "INSERT INTO documents (text, metadata) VALUES (?, ?)",
                (row["text"], row.get("metadata")),
            )
        await db.commit()


async def _get_all_metadata(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT id, text, metadata FROM documents ORDER BY id")
        rows = await cursor.fetchall()
    result = []
    for row in rows:
        meta_raw = row[2]
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {"_raw": meta_raw}
        else:
            meta = {}
        result.append({"id": row[0], "text": row[1], "metadata": meta})
    return result


# ===========================================================================
# 一、迁移正确性测试
# ===========================================================================

@pytest.mark.asyncio
async def test_migrate_private_chat_legacy_record(tmp_path):
    """私聊旧记录迁移后应补充 summary_schema_version=v1，原有字段不丢失。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": PRIVATE_MEMORY_LONG, "metadata": json.dumps(PRIVATE_METADATA_V1)},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]

    assert meta["summary_schema_version"] == "v1"
    assert meta["summary_quality"] == "unknown"
    assert meta["importance"] == 0.85
    assert meta["interaction_type"] == "private_chat"
    assert "后端工程师，使用 Python 和 Go" in meta["key_facts"]
    assert "participants" not in meta  # 私聊无参与者字段


@pytest.mark.asyncio
async def test_migrate_group_chat_legacy_record(tmp_path):
    """群聊旧记录迁移后应补充 summary_schema_version=v1，participants 字段保留。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": GROUP_MEMORY_LONG, "metadata": json.dumps(GROUP_METADATA_V1)},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]

    assert meta["summary_schema_version"] == "v1"
    assert meta["summary_quality"] == "unknown"
    assert meta["interaction_type"] == "group_chat"
    assert meta["participants"] == ["张三", "李四", "王五"]
    assert meta["importance"] == 0.78


@pytest.mark.asyncio
async def test_migrate_null_metadata_record(tmp_path):
    """metadata 为 NULL 的旧记录迁移后应正确补充字段。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": "用户说了一些话", "metadata": None},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]
    assert meta.get("summary_schema_version") == "v1"
    assert meta.get("summary_quality") == "unknown"


@pytest.mark.asyncio
async def test_migrate_empty_string_metadata_record(tmp_path):
    """metadata 为空字符串的旧记录迁移后应正确补充字段。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": "用户说了一些话", "metadata": ""},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]
    assert meta.get("summary_schema_version") == "v1"


@pytest.mark.asyncio
async def test_migrate_v2_record_not_overwritten(tmp_path):
    """已有 summary_schema_version=v2 的新记录不应被迁移覆盖。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": PRIVATE_MEMORY_LONG, "metadata": json.dumps(PRIVATE_METADATA_V2)},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]
    assert meta["summary_schema_version"] == "v2"
    assert meta["summary_quality"] == "normal"
    assert meta["canonical_summary"] == PRIVATE_METADATA_V2["canonical_summary"]


@pytest.mark.asyncio
async def test_migrate_mixed_private_and_group_records(tmp_path):
    """私聊和群聊旧记录混合时，全部正确迁移。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": PRIVATE_MEMORY_LONG, "metadata": json.dumps(PRIVATE_METADATA_V1)},
        {"text": GROUP_MEMORY_LONG, "metadata": json.dumps(GROUP_METADATA_V1)},
        {"text": "另一条私聊记忆", "metadata": json.dumps({"importance": 0.5, "interaction_type": "private_chat"})},
        {"text": "另一条群聊记忆", "metadata": json.dumps({"importance": 0.6, "interaction_type": "group_chat", "participants": ["用户A"]})},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    assert len(records) == 4
    for rec in records:
        assert rec["metadata"]["summary_schema_version"] == "v1"
        assert rec["metadata"]["summary_quality"] == "unknown"


@pytest.mark.asyncio
async def test_migrate_idempotent(tmp_path):
    """重复执行迁移不改变已迁移数据，字段不重复。"""
    db_path = str(tmp_path / "test.db")
    await _create_legacy_db(db_path, [
        {"text": PRIVATE_MEMORY_LONG, "metadata": json.dumps(PRIVATE_METADATA_V1)},
    ])

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    meta = records[0]["metadata"]
    assert meta["summary_schema_version"] == "v1"
    raw = json.dumps(meta)
    assert raw.count("summary_schema_version") == 1


@pytest.mark.asyncio
async def test_full_migration_v1_to_v4_with_real_data(tmp_path):
    """模拟真实 v1 数据库完整迁移到 v4，私聊和群聊各 3 条。"""
    db_path = str(tmp_path / "test.db")
    rows = []
    for i in range(3):
        rows.append({
            "text": PRIVATE_MEMORY_LONG + f"（第{i+1}次对话）",
            "metadata": json.dumps({**PRIVATE_METADATA_V1, "importance": round(0.7 + i * 0.05, 2)}),
        })
    for i in range(3):
        rows.append({
            "text": GROUP_MEMORY_LONG + f"（第{i+1}次群聊）",
            "metadata": json.dumps({**GROUP_METADATA_V1, "importance": round(0.6 + i * 0.05, 2)}),
        })
    await _create_legacy_db(db_path, rows)

    migration = DBMigration(db_path)
    result = await migration.migrate()

    assert result["success"] is True
    assert result["to_version"] == DBMigration.CURRENT_VERSION

    records = await _get_all_metadata(db_path)
    assert len(records) == 6
    for rec in records:
        assert rec["metadata"]["summary_schema_version"] == "v1"


@pytest.mark.asyncio
async def test_bulk_migration_100_records(tmp_path):
    """100 条旧记录（私聊/群聊各半）迁移后全部补充标记。"""
    db_path = str(tmp_path / "bulk.db")
    rows = []
    for i in range(50):
        rows.append({
            "text": f"私聊记忆内容 {i}：" + "用户描述了详细的个人信息和偏好。" * 5,
            "metadata": json.dumps({
                "importance": round(0.3 + (i % 7) * 0.1, 1),
                "topics": [f"话题{i}", "日常"],
                "key_facts": [f"事实{i}A", f"事实{i}B"],
                "interaction_type": "private_chat",
                "session_id": f"aiocqhttp:FriendMessage:{10000 + i}",
            }),
        })
    for i in range(50):
        rows.append({
            "text": f"群聊记忆内容 {i}：" + "群成员讨论了各种话题，达成了一些共识。" * 5,
            "metadata": json.dumps({
                "importance": round(0.4 + (i % 6) * 0.1, 1),
                "topics": [f"群话题{i}"],
                "key_facts": [f"群事实{i}"],
                "participants": [f"成员{i}A", f"成员{i}B"],
                "interaction_type": "group_chat",
                "session_id": f"aiocqhttp:GroupMessage:{88000 + i}",
            }),
        })
    await _create_legacy_db(db_path, rows)

    migration = DBMigration(db_path)
    await migration.initialize_version_table()
    await migration._migrate_v3_to_v4(None, None)

    records = await _get_all_metadata(db_path)
    assert len(records) == 100
    v1_count = sum(1 for r in records if r["metadata"].get("summary_schema_version") == "v1")
    assert v1_count == 100


# ===========================================================================
# 二、迁移后注入效果测试
# ===========================================================================

def test_format_injection_private_chat_v1_legacy():
    """私聊旧数据（v1）注入格式化：content 被正确展示，key_facts 和 topics 可见。"""
    memories = [
        {
            "content": PRIVATE_MEMORY_LONG,
            "score": 0.88,
            "timestamp": time.time() - 86400 * 15,
            "metadata": {**PRIVATE_METADATA_V1, "summary_schema_version": "v1", "summary_quality": "unknown"},
        },
    ]
    result = format_memories_for_injection(memories)

    assert result != ""
    assert "后端工程师" in result
    assert "工作" in result          # topics
    assert "后端工程师，使用 Python 和 Go" in result  # key_facts


def test_format_injection_group_chat_v1_legacy():
    """群聊旧数据（v1）注入格式化：participants 字段被展示。"""
    memories = [
        {
            "content": GROUP_MEMORY_LONG,
            "score": 0.82,
            "timestamp": time.time() - 86400 * 7,
            "metadata": {**GROUP_METADATA_V1, "summary_schema_version": "v1", "summary_quality": "unknown"},
        },
    ]
    result = format_memories_for_injection(memories)

    assert result != ""
    assert "张三" in result          # participants
    assert "AI工具" in result        # topics
    assert "建议公司内部部署私有化 LLM" in result  # key_facts


def test_format_injection_private_chat_v2_new():
    """私聊新数据（v2）注入格式化：canonical_summary 内容通过 content 字段展示。"""
    memories = [
        {
            "content": PRIVATE_METADATA_V2["canonical_summary"] + " | " + "；".join(PRIVATE_METADATA_V2["key_facts"][:5]),
            "score": 0.95,
            "timestamp": time.time() - 3600,
            "metadata": PRIVATE_METADATA_V2,
        },
    ]
    result = format_memories_for_injection(memories)

    assert result != ""
    assert "后端工程师" in result
    assert "Rust" in result


def test_format_injection_group_chat_v2_new():
    """群聊新数据（v2）注入格式化：participants 和 key_facts 均展示。"""
    memories = [
        {
            "content": GROUP_METADATA_V2["canonical_summary"] + " | " + "；".join(GROUP_METADATA_V2["key_facts"][:5]),
            "score": 0.91,
            "timestamp": time.time() - 7200,
            "metadata": GROUP_METADATA_V2,
        },
    ]
    result = format_memories_for_injection(memories)

    assert result != ""
    assert "张三" in result
    assert "私有化 LLM" in result


def test_format_injection_mixed_v1_v2_private_and_group():
    """新旧数据、私聊群聊混合时，全部正常格式化，无崩溃。"""
    now = time.time()
    memories = [
        # 私聊 v2
        {
            "content": PRIVATE_METADATA_V2["canonical_summary"],
            "score": 0.95,
            "timestamp": now - 1800,
            "metadata": PRIVATE_METADATA_V2,
        },
        # 群聊 v2
        {
            "content": GROUP_METADATA_V2["canonical_summary"],
            "score": 0.90,
            "timestamp": now - 3600,
            "metadata": GROUP_METADATA_V2,
        },
        # 私聊 v1（旧数据）
        {
            "content": PRIVATE_MEMORY_LONG,
            "score": 0.75,
            "timestamp": now - 86400 * 30,
            "metadata": {**PRIVATE_METADATA_V1, "summary_schema_version": "v1", "summary_quality": "unknown"},
        },
        # 群聊 v1（旧数据）
        {
            "content": GROUP_MEMORY_LONG,
            "score": 0.70,
            "timestamp": now - 86400 * 60,
            "metadata": {**GROUP_METADATA_V1, "summary_schema_version": "v1", "summary_quality": "unknown"},
        },
        # 极旧数据（无 schema_version）
        {
            "content": "用户很久以前提到过喜欢看电影",
            "score": 0.55,
            "timestamp": now - 86400 * 180,
            "metadata": {"importance": 0.4, "interaction_type": "private_chat"},
        },
    ]

    result = format_memories_for_injection(memories)

    assert result != ""
    assert "后端工程师" in result
    assert "张三" in result
    assert "私有化 LLM" in result
    assert "用户很久以前提到过喜欢看电影" in result
    # 5 条记忆全部出现
    assert result.count("记忆 #") == 5


def test_format_injection_long_content_does_not_crash():
    """超长 content（>2000字）不应导致格式化崩溃。"""
    long_content = "用户详细描述了自己的生活经历。" * 200  # ~2800 字
    memories = [
        {
            "content": long_content,
            "score": 0.7,
            "timestamp": time.time() - 86400,
            "metadata": {
                "importance": 0.6,
                "topics": ["生活"],
                "key_facts": ["用户有丰富的生活经历"],
                "interaction_type": "private_chat",
                "summary_schema_version": "v1",
                "summary_quality": "unknown",
            },
        },
    ]
    result = format_memories_for_injection(memories)
    assert result != ""
    assert "记忆 #1" in result


def test_format_injection_group_chat_many_participants():
    """群聊记忆有大量参与者时，格式化正常。"""
    memories = [
        {
            "content": "群聊中大家讨论了年终总结的写法",
            "score": 0.65,
            "timestamp": time.time() - 86400 * 3,
            "metadata": {
                "importance": 0.7,
                "topics": ["工作", "年终总结"],
                "key_facts": ["建议用数据说话", "突出个人贡献"],
                "participants": [f"成员{i}" for i in range(20)],
                "interaction_type": "group_chat",
                "summary_schema_version": "v1",
                "summary_quality": "unknown",
            },
        },
    ]
    result = format_memories_for_injection(memories)
    assert result != ""
    assert "成员0" in result
    assert "年终总结" in result


def test_format_injection_empty_list_returns_empty_string():
    """空记忆列表应返回空字符串。"""
    result = format_memories_for_injection([])
    assert result == ""


def test_format_injection_minimal_metadata_no_crash():
    """极简 metadata（只有 importance）不应崩溃。"""
    memories = [
        {
            "content": "用户提到了一些事情",
            "score": 0.5,
            "timestamp": None,
            "metadata": {"importance": 0.3},
        },
    ]
    result = format_memories_for_injection(memories)
    assert result != ""
    assert "用户提到了一些事情" in result
