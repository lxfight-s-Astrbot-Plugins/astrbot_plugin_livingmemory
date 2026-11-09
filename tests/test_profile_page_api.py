"""用户档案 Page API：列表/详情/历史/编辑/删除/待确认与就绪语义。"""

from contextlib import contextmanager
from pathlib import Path

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.page_api import PluginPageApi

from .test_memory_engine import _FakeFaissDB
from .test_page_api import FakePlugin, _mock_page_request

SCOPE = "webchat:FriendMessage:profile-api"
PERSONA = "persona-api"


async def _make_profile_api(
    tmp_path: Path, *, profile_enabled: bool = True
) -> tuple[MemoryEngine, PluginPageApi, dict]:
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config={
            "recent_memory_count": 0,
            "atom_enabled": False,
            "graph_memory_enabled": False,
            "profile_enabled": profile_enabled,
        },
    )
    await engine.initialize()
    memory_id = await engine.add_memory(
        content="用户喜欢燕麦拿铁",
        session_id=SCOPE,
        persona_id=PERSONA,
        importance=0.8,
    )
    await engine.profile_manager.apply_updates(
        SCOPE,
        PERSONA,
        [
            {
                "section": "basic",
                "field": "称呼",
                "value": "小林",
                "operation": "set",
                "evidence": [memory_id],
                "confidence": 0.9,
            }
        ],
        [memory_id],
    )
    await engine.profile_manager.apply_updates(
        SCOPE,
        PERSONA,
        [
            {
                "section": "preferences",
                "field": "饮品",
                "value": "美式",
                "operation": "set",
                "evidence": [],
                "confidence": 0.2,
            }
        ],
        [memory_id],
    )
    profile = await engine.profile_manager.get_profile(SCOPE, PERSONA)
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    return engine, api, profile


@contextmanager
def _patch_profile_request(req):
    import astrbot_plugin_livingmemory.core.page_api_modules.profile_handler as mod

    old = mod.request
    mod.request = req
    try:
        yield
    finally:
        mod.request = old


@pytest.mark.asyncio
async def test_list_profiles_and_single_lookup(tmp_path: Path):
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        req = _mock_page_request(args={"scope": SCOPE})
        with _patch_profile_request(req):
            result = await api.list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        item = result["data"]["profiles"][0]
        assert item["version"] == 1
        assert item["profile"]["basic"]["称呼"] == "小林"
        assert item["updated_at"] > 0
        assert api.plugin.readiness_waits[-1] is False

        req = _mock_page_request(args={"scope": SCOPE, "persona_id": PERSONA})
        with _patch_profile_request(req):
            single = await api.list_profiles()
        assert single["data"]["total"] == 1
        assert single["data"]["profile"]["id"] == profile["id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_detail_and_history(tmp_path: Path):
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        with _patch_profile_request(
            _mock_page_request(args={"id": str(profile["id"])})
        ):
            detail = await api.get_profile_detail()
        assert detail["status"] == "ok"
        assert detail["data"]["profile"]["pending"][0]["field"] == "饮品"

        with _patch_profile_request(
            _mock_page_request(args={"id": str(profile["id"]), "limit": "5"})
        ):
            history = await api.list_profile_history()
        assert history["status"] == "ok"
        assert history["data"]["total"] == 1
        assert history["data"]["history"][0]["changed_fields"] == ["basic.称呼"]

        with _patch_profile_request(_mock_page_request(args={"id": "999"})):
            missing = await api.get_profile_detail()
        assert missing["status"] == "error"
        assert "不存在" in missing["message"]

        with _patch_profile_request(_mock_page_request(args={"id": "bad"})):
            invalid = await api.get_profile_detail()
        assert invalid["status"] == "error"
        assert "正整数" in invalid["message"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_profile_patch_and_full_replace(tmp_path: Path):
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        req = _mock_page_request(
            get_json={"id": profile["id"], "patch": {"basic": {"职业": "工程师"}}}
        )
        with _patch_profile_request(req):
            updated = await api.update_profile()
        assert updated["status"] == "ok"
        assert updated["data"]["profile"]["version"] == 2
        assert updated["data"]["profile"]["profile"]["basic"]["称呼"] == "小林"
        assert updated["data"]["profile"]["profile"]["basic"]["职业"] == "工程师"
        assert api.plugin.readiness_waits[-1] is True

        req = _mock_page_request(
            get_json={
                "id": profile["id"],
                "profile_json": {"status": {"当前状态": "学习 Rust"}},
            }
        )
        with _patch_profile_request(req):
            replaced = await api.update_profile()
        assert replaced["data"]["profile"]["profile"]["basic"] == {}
        assert (
            replaced["data"]["profile"]["profile"]["status"]["当前状态"] == "学习 Rust"
        )

        req = _mock_page_request(get_json={"id": profile["id"]})
        with _patch_profile_request(req):
            missing_payload = await api.update_profile()
        assert missing_payload["status"] == "error"
        assert "缺少" in missing_payload["message"]

        req = _mock_page_request(get_json={"id": 999, "patch": {"basic": {"x": "y"}}})
        with _patch_profile_request(req):
            missing = await api.update_profile()
        assert missing["status"] == "error"
        assert "不存在" in missing["message"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_delete_profile_removes_row_and_history(tmp_path: Path):
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        req = _mock_page_request(get_json={"id": profile["id"]})
        with _patch_profile_request(req):
            deleted = await api.delete_profile()
        assert deleted["status"] == "ok"
        assert deleted["data"]["deleted"] is True
        assert await engine.profile_manager.get_profile(SCOPE, PERSONA) is None
        async with aiosqlite.connect(engine.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memory_profile_history WHERE profile_id = ?",
                (profile["id"],),
            )
            assert (await cursor.fetchone())[0] == 0

        with _patch_profile_request(req):
            again = await api.delete_profile()
        assert again["status"] == "error"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_pending_list_accept_and_reject(tmp_path: Path):
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        req = _mock_page_request(args={"scope": SCOPE, "persona_id": PERSONA})
        with _patch_profile_request(req):
            pending = await api.list_profile_pending()
        assert pending["status"] == "ok"
        assert pending["data"]["total"] == 1
        assert pending["data"]["pending"][0]["section"] == "preferences"

        accept_req = _mock_page_request(
            get_json={
                "scope": SCOPE,
                "persona_id": PERSONA,
                "index": 0,
                "action": "accept",
            }
        )
        with _patch_profile_request(accept_req):
            accepted = await api.resolve_profile_pending()
        assert accepted["status"] == "ok"
        assert accepted["data"]["profile"]["profile"]["preferences"]["饮品"] == ["美式"]
        assert accepted["data"]["profile"]["pending"] == []

        # reject path: add another pending candidate
        await engine.profile_manager.apply_updates(
            SCOPE,
            PERSONA,
            [
                {
                    "section": "status",
                    "field": "状态",
                    "value": "加班",
                    "operation": "set",
                    "confidence": 0.2,
                }
            ],
            [],
        )
        reject_req = _mock_page_request(
            get_json={
                "scope": SCOPE,
                "persona_id": PERSONA,
                "index": 0,
                "action": "reject",
            }
        )
        with _patch_profile_request(reject_req):
            rejected = await api.resolve_profile_pending()
        assert rejected["status"] == "ok"
        assert rejected["data"]["profile"]["pending"] == []

        bad_action = _mock_page_request(
            get_json={
                "scope": SCOPE,
                "persona_id": PERSONA,
                "index": 0,
                "action": "maybe",
            }
        )
        with _patch_profile_request(bad_action):
            bad = await api.resolve_profile_pending()
        assert bad["status"] == "error"
        assert "accept" in bad["message"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_api_not_ready_quick_fail(tmp_path: Path):
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config={"atom_enabled": False, "graph_memory_enabled": False},
    )
    await engine.initialize()
    try:
        plugin = FakePlugin(ready=False, memory_engine=engine)
        api = PluginPageApi(plugin)
        req = _mock_page_request(args={"scope": SCOPE})
        with _patch_profile_request(req):
            read = await api.list_profiles()
        assert read["status"] == "error"
        assert read["code"] == "not_ready"

        req = _mock_page_request(get_json={"id": 1, "patch": {"basic": {"x": "y"}}})
        with _patch_profile_request(req):
            write = await api.update_profile()
        assert write["status"] == "error"
        assert write["code"] == "not_ready"
    finally:
        await engine.close()


def test_profile_routes_register_post_aliases():
    """宿主 bridge 只支持 apiGet/apiPost：update/delete 必须同时接受 POST。"""
    from astrbot_plugin_livingmemory.core.page_api import PAGE_API_PREFIX

    plugin = FakePlugin()
    api = PluginPageApi(plugin)
    api.register_routes()
    methods_by_path = {
        route: sorted(methods) for route, _, methods, _ in plugin._api_routes
    }
    assert methods_by_path[f"{PAGE_API_PREFIX}/profiles/update"] == ["POST", "PUT"]
    assert methods_by_path[f"{PAGE_API_PREFIX}/profiles/delete"] == ["DELETE", "POST"]
    assert methods_by_path[f"{PAGE_API_PREFIX}/profiles"] == ["GET"]
    assert methods_by_path[f"{PAGE_API_PREFIX}/profiles/pending"] == ["GET"]
    assert methods_by_path[f"{PAGE_API_PREFIX}/profiles/pending/resolve"] == ["POST"]


@pytest.mark.asyncio
async def test_post_alias_behaves_like_put_and_delete(tmp_path: Path):
    """POST 别名与 PUT/DELETE 共用 handler：编辑/删除结果与版本语义一致。"""
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        put_req = _mock_page_request(
            method="PUT",
            get_json={"id": profile["id"], "patch": {"basic": {"职业": "工程师"}}},
        )
        with _patch_profile_request(put_req):
            put_result = await api.update_profile()
        assert put_result["status"] == "ok"
        assert put_result["data"]["profile"]["version"] == 2

        post_req = _mock_page_request(
            method="POST",
            get_json={"id": profile["id"], "patch": {"basic": {"城市": "杭州"}}},
        )
        with _patch_profile_request(post_req):
            post_result = await api.update_profile()
        assert post_result["status"] == "ok"
        assert post_result["data"]["profile"]["version"] == 3
        assert post_result["data"]["profile"]["profile"]["basic"]["城市"] == "杭州"
        history = await engine.profile_manager.get_history(profile["id"])
        assert history[0]["source"] == "manual"
        assert "basic.城市" in history[0]["changed_fields"]

        delete_req = _mock_page_request(method="POST", get_json={"id": profile["id"]})
        with _patch_profile_request(delete_req):
            deleted = await api.delete_profile()
        assert deleted["status"] == "ok"
        assert deleted["data"]["deleted"] is True
        assert await engine.profile_manager.get_profile(SCOPE, PERSONA) is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_list_profiles_without_scope_returns_everything(tmp_path: Path):
    """GET /page/profiles 未传 scope 时返回全部档案。"""
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        other_scope = "webchat:GroupMessage:other"
        memory_id = await engine.add_memory(
            content="另一个作用域的证据",
            session_id=other_scope,
            persona_id=None,
            importance=0.7,
        )
        await engine.profile_manager.apply_updates(
            other_scope,
            None,
            [
                {
                    "section": "basic",
                    "field": "称呼",
                    "value": "阿青",
                    "operation": "set",
                    "evidence": [memory_id],
                    "confidence": 0.9,
                }
            ],
            [memory_id],
        )
        with _patch_profile_request(_mock_page_request(args={})):
            result = await api.list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        scopes = {item["scope"] for item in result["data"]["profiles"]}
        assert scopes == {SCOPE, other_scope}
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_detail_includes_pending_array(tmp_path: Path):
    """详情接口必须返回 pending 数组（前端据此展示待确认候选）。"""
    engine, api, profile = await _make_profile_api(tmp_path)
    try:
        with _patch_profile_request(
            _mock_page_request(args={"id": str(profile["id"])})
        ):
            detail = await api.get_profile_detail()
        pending = detail["data"]["profile"]["pending"]
        assert isinstance(pending, list)
        assert pending and pending[0]["section"] == "preferences"
        assert detail["data"]["profile"]["version"] == 1
        assert detail["data"]["profile"]["updated_at"] > 0
    finally:
        await engine.close()
