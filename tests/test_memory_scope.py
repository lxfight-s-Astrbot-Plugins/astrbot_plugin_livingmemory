"""Memory access, identity mapping, and scope resolution tests."""

import pytest

from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.memory_scope import (
    GLOBAL_MEMORY_SCOPE,
    is_event_memory_allowed,
    parse_identity_aliases,
    parse_value_list,
    resolve_event_identity,
    resolve_memory_scope,
    resolve_sender_alias,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore


class _Event:
    def __init__(
        self,
        session_id: str = "test:private:session-1",
        sender_id: str = "user-1",
        sender_name: str = "Original Name",
        platform: str = "test",
        group_id: str = "",
        self_id: str = "bot-1",
    ) -> None:
        self.unified_msg_origin = session_id
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.platform = platform
        self.group_id = group_id
        self.self_id = self_id

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_platform_name(self):
        return self.platform

    def get_platform_id(self):
        return f"{self.platform}-instance"

    def get_self_id(self):
        return self.self_id

    def get_group_id(self):
        return self.group_id


def _config(**overrides):
    config = {
        "access_control": {
            "whitelist_enabled": False,
            "allowed_ids": "",
            "identity_aliases": "",
        },
        "filtering_settings": {
            "memory_scope_mode": "legacy",
            "use_session_filtering": True,
            "isolated_sessions": "",
        },
    }
    for section, values in overrides.items():
        config[section].update(values)
    return config


def test_parse_lists_and_aliases():
    assert parse_value_list("one, two;three\none") == ["one", "two", "three"]
    assert parse_identity_aliases("test:user-1=Alex\nOriginal Name=Alice") == {
        "test:user-1": "Alex",
        "original name": "Alice",
    }


@pytest.mark.parametrize(
    "allowed_ids",
    [
        "user-1",
        "test:user-1",
        "test:private:session-1",
        "group-1",
        "Canonical Name",
    ],
)
def test_whitelist_accepts_supported_identifiers(allowed_ids):
    config = _config(
        access_control={
            "whitelist_enabled": True,
            "allowed_ids": allowed_ids,
            "identity_aliases": "test:user-1=Canonical Name",
        }
    )
    assert is_event_memory_allowed(config, _Event(group_id="group-1")) is True


def test_enabled_empty_whitelist_denies_access():
    config = _config(
        access_control={"whitelist_enabled": True, "allowed_ids": ""}
    )
    assert is_event_memory_allowed(config, _Event()) is False


def test_alias_priority_and_sender_mapping():
    config = _config(
        access_control={
            "identity_aliases": (
                "Original Name=By Name\nuser-1=By ID\ntest:user-1=By Platform"
            )
        }
    )
    assert resolve_event_identity(config, _Event()) == "By Platform"
    assert (
        resolve_sender_alias(
            config["access_control"]["identity_aliases"],
            "test",
            "user-1",
            "Original Name",
        )
        == "By Platform"
    )


def test_scope_modes_and_isolated_override():
    first = _Event(session_id="test:private:first")
    second = _Event(session_id="test:group:second")
    user_config = _config(filtering_settings={"memory_scope_mode": "user"})
    assert resolve_memory_scope(user_config, first) == resolve_memory_scope(
        user_config, second
    )

    assert resolve_memory_scope(
        _config(filtering_settings={"memory_scope_mode": "global"}), first
    ) == GLOBAL_MEMORY_SCOPE
    assert resolve_memory_scope(
        _config(filtering_settings={"memory_scope_mode": "session"}), first
    ) == first.unified_msg_origin
    assert (
        resolve_memory_scope(
            _config(
                filtering_settings={
                    "memory_scope_mode": "global",
                    "isolated_sessions": first.unified_msg_origin,
                }
            ),
            first,
        )
        == first.unified_msg_origin
    )


def test_legacy_scope_compatibility():
    event = _Event()
    assert resolve_memory_scope(_config(), event) == event.unified_msg_origin
    assert (
        resolve_memory_scope(
            _config(filtering_settings={"use_session_filtering": False}), event
        )
        is None
    )


def test_legacy_shared_pool_excludes_configured_isolated_sessions():
    shared = _Event(session_id="test:private:shared")
    isolated = _Event(session_id="test:private:isolated")
    config = _config(
        filtering_settings={
            "use_session_filtering": False,
            "isolated_sessions": isolated.unified_msg_origin,
        }
    )

    assert resolve_memory_scope(config, shared) == GLOBAL_MEMORY_SCOPE
    assert resolve_memory_scope(config, isolated) == isolated.unified_msg_origin


@pytest.mark.asyncio
async def test_conversation_manager_applies_user_alias_only_to_user_messages(tmp_path):
    store = ConversationStore(str(tmp_path / "aliases.db"))
    await store.initialize()
    manager = ConversationManager(
        store=store,
        identity_aliases="test:user-1=Canonical Name",
    )
    event = _Event()

    user_message = await manager.add_message_from_event(event, "user", "hello")
    assistant_message = await manager.add_message_from_event(event, "assistant", "hi")

    assert user_message.sender_name == "Canonical Name"
    assert assistant_message.sender_id == "bot-1"
    assert assistant_message.sender_name == "bot-1"
    await store.close()
