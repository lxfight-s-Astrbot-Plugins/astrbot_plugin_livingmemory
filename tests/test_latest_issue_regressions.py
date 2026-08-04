"""Regression coverage for the newest graph, summary, and dashboard issues."""

from pathlib import Path

from astrbot_plugin_livingmemory.core.models.memory_atom import MemoryAtom
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "pages" / "dashboard"


def _identity(display_name: str, *, is_bot: bool = False) -> dict:
    return {
        "identity_key": "aiocqhttp:10001",
        "sender_id": "10001",
        "platform": "aiocqhttp",
        "display_name": display_name,
        "aliases": [display_name],
        "is_bot": is_bot,
    }


def _graph_metadata(display_name: str) -> dict:
    return {
        "canonical_summary": f"{display_name}确认了发布计划",
        "topics": ["发布计划"],
        "key_facts": [f"{display_name}确认周五发布"],
        "participants": [display_name],
        "participant_identities": [_identity(display_name)],
    }


def test_person_node_key_survives_nickname_changes() -> None:
    extractor = GraphExtractor()

    before = extractor.extract(1, "旧昵称确认了发布计划", _graph_metadata("旧昵称"))
    after = extractor.extract(2, "新昵称确认了发布计划", _graph_metadata("新昵称"))

    before_people = [node for node in before.nodes if node.node_type == "person"]
    after_people = [node for node in after.nodes if node.node_type == "person"]
    assert [node.node_key for node in before_people] == [
        "person:account:aiocqhttp:10001"
    ]
    assert [node.node_key for node in after_people] == [
        "person:account:aiocqhttp:10001"
    ]
    assert after_people[0].value == "新昵称"


def test_atom_graph_uses_stable_person_nodes_instead_of_name_topics() -> None:
    atom = MemoryAtom(
        parent_memory_id=1,
        content="寒露确认周五发布",
        entities=["发布计划", "寒露"],
    )
    metadata = _graph_metadata("寒露")

    graph = GraphExtractor().extract(1, atom.content, metadata, [atom])

    assert any(
        node.node_key == "person:account:aiocqhttp:10001" for node in graph.nodes
    )
    assert not any(
        node.node_type == "topic" and node.canonical_value == "寒露"
        for node in graph.nodes
    )
    assert any(edge.relation_type == "mentioned_in" for edge in graph.edges)


def test_participant_identity_keeps_alias_history_and_latest_display_name() -> None:
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    messages = [
        Message(1, "s1", "user", "你好", "10001", "旧昵称", platform="aiocqhttp"),
        Message(2, "s1", "user", "改名了", "10001", "新昵称", platform="aiocqhttp"),
    ]

    identities = MemoryProcessor._extract_participant_identities(messages)

    assert identities == [
        {
            "identity_key": "aiocqhttp:10001",
            "sender_id": "10001",
            "platform": "aiocqhttp",
            "display_name": "新昵称",
            "aliases": ["旧昵称", "新昵称"],
            "is_bot": False,
        }
    ]


def test_dashboard_prefers_persona_summary_for_display() -> None:
    memory_page = (DASHBOARD / "modules" / "memory-page.js").read_text(
        encoding="utf-8"
    )
    utils = (DASHBOARD / "modules" / "utils.js").read_text(encoding="utf-8")

    assert "item.metadata.persona_summary" in memory_page
    assert "detail.summary || detail.text" in utils


def test_graph_toolbar_has_intermediate_desktop_breakpoints() -> None:
    css = (DASHBOARD / "art-direction.css").read_text(encoding="utf-8")

    assert "@media (min-width: 769px) and (max-width: 1600px)" in css
    assert "grid-column: 1 / -1" in css
    assert "#graph-query-input" in css
    assert "#graph-memory-id" in css


def test_summary_prompts_keep_persona_voice_and_single_summary_output() -> None:
    """内置提示词保留人格化主观视角要求，且只要求输出一份 summary。

    2.4.0 曾让模型同时产出 summary 与 canonical_summary 两份风格相反的摘要，
    导致总结质量下降；回退后检索语料由代码从 summary + key_facts 组装。
    """
    for prompt_name in ("private_chat_prompt.txt", "group_chat_prompt.txt"):
        prompt = (ROOT / "core" / "prompts" / prompt_name).read_text(encoding="utf-8")
        assert "主观回忆" in prompt
        assert "canonical_summary" not in prompt
