"""回归：旧库里已从 AtomType 移除的类型（例如 1.x 的 rule）不应让 atoms 路由失效。

背景：AtomType 枚举收敛后不再包含 "rule"，旧库里的这类行会让 _row_to_atom()
抛 ValueError；而 route_execution.search_route() 捕获异常后返回空列表，
于是整条 atoms 检索路由被静默清空，不产生 ERROR。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.models.memory_atom import AtomType, MemoryAtom
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.storage.atom_store import AtomStore


async def _store_with_atom(
    tmp_path: Path, atom_type: str | None
) -> tuple[AtomStore, int]:
    store = AtomStore(str(tmp_path / "atoms.db"), text_processor=TextProcessor())
    await store.initialize()
    atom_id = await store.insert(
        MemoryAtom(parent_memory_id=1, content="旧版本写入的规则记忆")
    )
    if atom_type is not None:
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET atom_type = ? WHERE id = ?",
                (atom_type, atom_id),
            )
            await db.commit()
    return store, atom_id


@pytest.mark.asyncio
async def test_legacy_atom_type_row_is_readable(tmp_path: Path) -> None:
    """atom_type='rule'（旧版本遗留）读出来应降级为 unknown，而不是抛 ValueError。"""
    store, atom_id = await _store_with_atom(tmp_path, "rule")

    atom = await store.get(atom_id)
    assert atom is not None
    assert atom.atom_type is AtomType.UNKNOWN

    by_parent = await store.get_by_parent(1)
    assert [a.atom_type for a in by_parent] == [AtomType.UNKNOWN]

    hits = await store.search_fts("规则记忆", limit=5)
    assert [a.atom_type for a in hits] == [AtomType.UNKNOWN]

    # reinforce 也要能跑（它同样按 atom_type 算 TTL）
    await store.reinforce(atom_id)
    reinforced = await store.get(atom_id)
    assert reinforced is not None
    assert reinforced.atom_type is AtomType.UNKNOWN
    await store.close()


@pytest.mark.asyncio
async def test_known_atom_type_is_preserved(tmp_path: Path) -> None:
    """合法类型不能被降级逻辑带偏。"""
    store, atom_id = await _store_with_atom(tmp_path, AtomType.FACTUAL.value)
    atom = await store.get(atom_id)
    assert atom is not None
    assert atom.atom_type is AtomType.FACTUAL
    await store.close()
