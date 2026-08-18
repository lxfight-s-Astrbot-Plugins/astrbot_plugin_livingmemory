"""
MemoryProcessor 的 MemoryProcessorBuildMixin 拆分模块
自动从 core/processors/memory_processor.py 拆分，保持行为不变
"""

from typing import Any
from .atom_classifier import classify_atoms
import json
from ..models.memory_atom import MemoryAtom


class MemoryProcessorBuildMixin:
    """MemoryProcessor 拆分模块：MemoryProcessorBuildMixin"""
    def _build_storage_format(
        self,
        fallback_excerpt: str,
        structured_data: dict[str, Any],
        is_group_chat: bool,
    ) -> tuple[str, dict[str, Any]]:
        """
        构建存储格式

        Args:
            fallback_excerpt: 当摘要为空时使用的对话摘录
            structured_data: 结构化数据
            is_group_chat: 是否为群聊

        Returns:
            (content, metadata) 元组
        """
        summary = str(structured_data.get("summary", "")).strip()
        key_facts = structured_data.get("key_facts", [])

        # 检索内容恒为 summary + key_facts 的富文本：content 是 BM25/FAISS 的
        # 索引语料，也是 Agent 主动召回工具直接返回给 LLM 的内容，必须保证信息
        # 密度。不依赖模型输出的压缩摘要，也不退化为纯事实的机械拼接。
        # （自动注入链路优先使用 metadata 中的 persona_summary，不受此影响。）
        rich_parts = [summary] if summary else []
        if key_facts:
            rich_parts.append("；".join(str(f) for f in key_facts[:5] if f))
        rich_content = " | ".join(rich_parts)

        content = rich_content if rich_content else fallback_excerpt

        # canonical_summary：自定义提示词若输出了该字段则保留（供图抽取等使用），
        # 否则回退为与 content 一致的富文本，保持 v2 metadata 结构兼容。
        canonical_summary = str(structured_data.get("canonical_summary") or "").strip()
        if not canonical_summary:
            canonical_summary = rich_content

        # metadata字段:存储结构化信息
        # 注意：不要在这里设置 create_time 和 last_access_time
        # 这些字段会由 MemoryEngine.add_memory() 自动添加
        metadata = {
            "topics": structured_data.get("topics", []),
            "key_facts": key_facts,
            "sentiment": structured_data.get("sentiment", "neutral"),
            "interaction_type": "group_chat" if is_group_chat else "private_chat",
            # 双通道：canonical_summary 供图抽取等中性文本消费方使用，
            # persona_summary 保留人格风格摘要供面板展示
            "canonical_summary": canonical_summary,
            "persona_summary": summary,
            "summary_schema_version": "v2",
            # summary_quality 由 process_conversation 中的 SummaryValidator 覆盖写入
        }

        if is_group_chat and "participants" in structured_data:
            metadata["participants"] = structured_data["participants"]

        return content, metadata

    def classify_atoms_from_metadata(
        self,
        metadata: dict[str, Any],
        parent_importance: float = 0.5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[MemoryAtom]:
        """Generate time-aware memory atoms from key_facts in metadata.

        This is a post-processing step after process_conversation().
        It does NOT make additional LLM calls — classification is rule-based.
        """
        if not self.config.get("atom_enabled", True):
            return []
        key_facts: list[str] = metadata.get("key_facts", [])
        if not key_facts:
            return []
        topics = metadata.get("topics", [])
        participants = metadata.get("participants", [])
        atoms = classify_atoms(
            key_facts=key_facts,
            topics=topics,
            participants=participants,
            parent_importance=parent_importance,
            session_id=session_id,
            persona_id=persona_id,
        )
        metadata["atom_types"] = sorted({atom.atom_type.value for atom in atoms})
        return atoms

    async def merge_memories(self, memories: list[dict]) -> dict[str, Any]:
        """把一组零散记忆合并为一条精炼记忆（供记忆库整合使用）。

        Args:
            memories: 待合并的记忆列表，每条为 {"content": str, "metadata": dict}。

        Returns:
            包含 summary/key_facts/topics/importance 的字典。

        Raises:
            RuntimeError: LLM 不可用或解析失败时抛出。
        """
        items: list[dict[str, Any]] = []
        for i, mem in enumerate(memories, 1):
            metadata = mem.get("metadata") or {}
            summary = str(
                metadata.get("persona_summary")
                or str(mem.get("content", "")).strip()
            ).strip()
            items.append(
                {
                    "id": i,
                    "summary": summary,
                    "key_facts": metadata.get("key_facts") or [],
                    "topics": metadata.get("topics") or [],
                }
            )

        system_prompt = (
            "你是记忆整理助手。把多条关于同一主题或会话的零散记忆合并为一条精炼、"
            "信息无损的记忆摘要。保留所有关键事实与具体细节，去重并消除相互矛盾，"
            "避免泛化和丢失专有名词。只输出 JSON，不要输出任何其他内容。"
        )
        prompt = (
            f"以下是一组需要合并的记忆（共 {len(items)} 条）：\n"
            f"{json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
            "请将它们合并为一条记忆，按如下 JSON 格式输出：\n"
            '{"summary": "合并后的精炼摘要", "key_facts": ["事实1", "事实2"], '
            '"topics": ["主题1"], "importance": 0.5}'
        )

        text = await self._call_llm_with_retry(prompt, system_prompt)
        data = self._parse_merge_response(text)

        summary = str(data.get("summary", "")).strip()
        if not summary:
            raise RuntimeError("合并结果缺少 summary")

        return {
            "summary": summary,
            "key_facts": self._ensure_list(data.get("key_facts", []))[:5],
            "topics": self._ensure_list(data.get("topics", []))[:5],
            "importance": self._validate_importance(data.get("importance", 0.5)),
        }

    def _parse_merge_response(self, text: str) -> dict[str, Any]:
        """解析合并 LLM 响应中的 JSON，失败时抛出异常。"""
        candidates = [text]
        fixed = self._try_fix_json(text)
        if fixed != text.strip():
            candidates.append(fixed)

        from ..utils import extract_json_from_response

        extracted = extract_json_from_response(text)
        if extracted != text.strip():
            candidates.append(extracted)

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                data = json.loads(self._try_fix_json(candidate))
            except (json.JSONDecodeError, TypeError) as e:
                last_error = e
                continue
            if isinstance(data, dict):
                return data

        raise RuntimeError(f"合并结果 JSON 解析失败: {last_error}")
