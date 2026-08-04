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
