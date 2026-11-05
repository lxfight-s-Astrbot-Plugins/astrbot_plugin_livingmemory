"""Issue #388 回归：注入文本使用参与者稳定身份区分同名与改名用户。"""

from astrbot_plugin_livingmemory.core.utils import format_memories_for_injection


def _identity(
    sender_id: str,
    display_name: str,
    *,
    is_bot: bool = False,
    aliases: list[str] | None = None,
) -> dict:
    return {
        "identity_key": f"aiocqhttp:{sender_id}",
        "sender_id": sender_id,
        "platform": "aiocqhttp",
        "display_name": display_name,
        "aliases": aliases or [display_name],
        "is_bot": is_bot,
    }


def _memory(metadata: dict) -> list[dict]:
    return [{"content": "小明和小刚约好周五发布", "score": 0.8, "metadata": metadata}]


def test_injection_distinguishes_same_name_participants_by_sender_id() -> None:
    result = format_memories_for_injection(
        _memory(
            {
                "participants": ["小明", "小明", "小刚"],
                "participant_identities": [
                    _identity("1001234", "小明"),
                    _identity("2005678", "小明"),
                    _identity("3009999", "小刚"),
                ],
            }
        )
    )

    assert "Participants: 小明#1234、小明#5678、小刚#9999" in result


def test_injection_keeps_sender_suffix_across_nickname_changes() -> None:
    before = format_memories_for_injection(
        _memory(
            {
                "participants": ["旧昵称"],
                "participant_identities": [
                    _identity("123456", "旧昵称", aliases=["旧昵称"])
                ],
            }
        )
    )
    after = format_memories_for_injection(
        _memory(
            {
                "participants": ["新昵称"],
                "participant_identities": [
                    _identity("123456", "新昵称", aliases=["旧昵称", "新昵称"])
                ],
            }
        )
    )

    assert "旧昵称#3456" in before
    assert "新昵称#3456" in after


def test_injection_renders_bot_identity_as_first_person() -> None:
    result = format_memories_for_injection(
        _memory(
            {
                "participants": ["小明", "我"],
                "participant_identities": [
                    _identity("1001234", "小明"),
                    _identity("8888", "小助手", is_bot=True),
                ],
            }
        )
    )

    assert "Participants: 小明#1234、我" in result


def test_injection_falls_back_to_participants_without_identities() -> None:
    result = format_memories_for_injection(_memory({"participants": ["张三", "李四"]}))

    assert "Participants: 张三、李四" in result
