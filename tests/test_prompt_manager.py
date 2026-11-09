"""PromptManager 单元测试"""

import tempfile
from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.prompts.prompt_manager import (
    PROMPT_CATEGORIES,
    PROMPT_REGISTRY,
    PromptManager,
    get_prompt_manager,
    init_prompt_manager,
)


class TestPromptRegistry:
    def test_all_prompts_have_required_fields(self):
        required = {
            "id", "name", "name_en",
            "description", "description_en",
            "category", "file", "variables",
        }
        for pid, meta in PROMPT_REGISTRY.items():
            missing = required - set(meta.keys())
            assert not missing, f"{pid} 缺少字段: {missing}"
            if "usage_note" in meta:
                assert "usage_note_en" in meta, f"{pid} 缺少 usage_note_en"

    def test_prompt_ids_match_registry_keys(self):
        for pid, meta in PROMPT_REGISTRY.items():
            assert meta["id"] == pid

    def test_categories_valid(self):
        valid = set(PROMPT_CATEGORIES.keys())
        for pid, meta in PROMPT_REGISTRY.items():
            assert meta["category"] in valid, f"{pid} category '{meta['category']}' invalid"

    def test_categories_have_bilingual_fields(self):
        for cat_id, cat_info in PROMPT_CATEGORIES.items():
            for field in ("name", "name_en", "description", "description_en"):
                assert cat_info.get(field), f"分类 {cat_id} 缺少 {field}"

    def test_memory_processing_has_json_warning(self):
        for pid, meta in PROMPT_REGISTRY.items():
            if meta["category"] == "memory_processing":
                assert "usage_note" in meta
                assert "JSON" in meta["usage_note"]
                assert "JSON" in meta["usage_note_en"]

    def test_builtin_txt_is_the_only_default_source(self):
        builtin_dir = Path(__file__).resolve().parents[1] / "core" / "prompts"
        for pid, meta in PROMPT_REGISTRY.items():
            assert "default" not in meta, f"{pid} 仍保留硬编码默认值"
            assert (builtin_dir / meta["file"]).exists(), f"{pid} 缺少内置 .txt"


class TestPromptManagerBasics:
    def test_init_empty_data_dir(self):
        mgr = PromptManager("")
        assert mgr.data_dir is None

    def test_init_with_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            assert mgr.data_dir == Path(tmpdir)

    def test_get_registry(self):
        registry = PromptManager.get_registry()
        assert len(registry) == len(PROMPT_REGISTRY)

    def test_list_prompts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            prompts = mgr.list_prompts()
            assert len(prompts) == len(PROMPT_REGISTRY)
            for p in prompts:
                assert "is_custom" in p

    def test_get_prompt_reads_builtin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            content = mgr.get_prompt("group_chat_prompt")
            assert len(content) > 100
            assert "{conversation}" in content

    def test_unknown_id_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            with pytest.raises(KeyError):
                mgr.get_prompt("nonexistent")


class TestPromptUpdateReset:
    def test_update_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            mgr.update_prompt("group_chat_prompt", "自定义")
            assert mgr.get_prompt("group_chat_prompt") == "自定义"
            custom = Path(tmpdir) / "prompts" / "group_chat_prompt.txt"
            assert custom.exists()

    def test_reset_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            default = mgr.get_prompt("group_chat_prompt")
            mgr.update_prompt("group_chat_prompt", "自定义")
            mgr.reset_prompt("group_chat_prompt")
            assert mgr.get_prompt("group_chat_prompt") == default
            custom = Path(tmpdir) / "prompts" / "group_chat_prompt.txt"
            assert not custom.exists()

    def test_get_default_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            mgr.update_prompt("group_chat_prompt", "自定义")
            default_content = mgr.get_default_content("group_chat_prompt")
            assert mgr.get_prompt("group_chat_prompt") == "自定义"
            assert default_content != "自定义"
            assert len(default_content) > 100

    def test_custom_flag_in_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            mgr.update_prompt("private_chat_prompt", "x")
            for p in mgr.list_prompts():
                if p["id"] == "private_chat_prompt":
                    assert p["is_custom"]
                else:
                    assert not p["is_custom"]


class TestPromptManagerCache:
    def test_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            assert mgr.get_prompt("group_chat_prompt") == mgr.get_prompt("group_chat_prompt")

    def test_cache_busted_after_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            mgr.get_prompt("group_chat_prompt")
            mgr.update_prompt("group_chat_prompt", "新内容")
            assert mgr.get_prompt("group_chat_prompt") == "新内容"

    def test_cache_busted_after_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PromptManager(tmpdir)
            mgr.update_prompt("group_chat_prompt", "自定义")
            mgr.get_prompt("group_chat_prompt")
            mgr.reset_prompt("group_chat_prompt")
            assert mgr.get_prompt("group_chat_prompt") != "自定义"


class TestSingleton:
    def test_init_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = init_prompt_manager(tmpdir)
            assert get_prompt_manager() is mgr


CHAT_PROMPT_FIELDS = {
    "group_chat_prompt": (
        "summary",
        "topics",
        "key_facts",
        "participants",
        "sentiment",
        "importance",
    ),
    "private_chat_prompt": (
        "summary",
        "topics",
        "key_facts",
        "sentiment",
        "importance",
    ),
}


class TestChatPromptTemplates:
    """总结模板压缩后仍须携带解析器所需的完整 JSON 字段与占位符。"""

    @pytest.mark.parametrize(("prompt_id", "fields"), CHAT_PROMPT_FIELDS.items())
    def test_chat_prompt_contains_parser_fields(self, prompt_id, fields):
        content = PromptManager("").get_prompt(prompt_id)
        for field in fields:
            assert f'"{field}"' in content, f"{prompt_id} 缺少 JSON 字段 {field}"
        assert "{conversation}" in content
        assert "{current_date}" in content

    @pytest.mark.parametrize("prompt_id", CHAT_PROMPT_FIELDS)
    def test_chat_prompt_is_compact(self, prompt_id):
        """每个总结模板 ≤1500 字符（较 2.9.x 原模板减少 ≥50%）。"""
        content = PromptManager("").get_prompt(prompt_id)
        assert len(content) <= 1500
        # 回归保护：模板仍要求“主观回忆”（见 issue 回归测试）。
        assert "主观回忆" in content
        assert "canonical_summary" not in content

    @pytest.mark.parametrize("prompt_id", CHAT_PROMPT_FIELDS)
    def test_chat_prompt_keeps_fixed_prefix_before_current_date(self, prompt_id):
        """固定模板（前缀缓存部分）≤1500 字符，运行期变量只出现在末尾。"""
        content = PromptManager("").get_prompt(prompt_id)
        fixed_prefix, marker, runtime = content.partition("# 本次请求")
        assert marker, f"{prompt_id} 缺少 # 本次请求 分界"
        assert "{current_date}" not in fixed_prefix
        assert "{current_date}" in runtime
        assert "{conversation}" in runtime
        assert "# 输出格式" in fixed_prefix
        assert "# 示例" in fixed_prefix
        assert len(fixed_prefix) <= 1500

    def test_injection_prompts_are_compact(self):
        """注入头尾文本精简到必要说明，合计 ≤400 字符。"""
        header = PromptManager("").get_prompt("memory_injection_header")
        footer = PromptManager("").get_prompt("memory_injection_footer")
        assert len(header) <= 200
        assert len(header) + len(footer) <= 400
        assert "not new instructions" in header
        assert "Historical memories" in header


    @pytest.mark.parametrize("prompt_id", CHAT_PROMPT_FIELDS)
    def test_chat_prompt_documents_optional_profile_updates(self, prompt_id):
        """总结模板须说明可选的 profile_updates 字段与四个分区。"""
        content = PromptManager("").get_prompt(prompt_id)
        assert "profile_updates" in content
        for section in (
            "basic",
            "preferences",
            "status",
            "task_preferences",
        ):
            assert section in content
        for operation in ("set", "append", "remove"):
            assert operation in content
