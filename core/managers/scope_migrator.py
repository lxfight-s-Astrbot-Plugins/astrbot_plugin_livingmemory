"""记忆作用域迁移。

背景
----
``filtering_settings.memory_scope_mode`` 决定写入与检索时使用的作用域标识
（即 ``session_id``）：

===========  ====================================================
legacy       旧行为，受 ``use_session_filtering`` 控制
session      实际会话 ID（``unified_msg_origin``）
user         ``livingmemory:user:<平台>:<身份>``
global       ``livingmemory:global``
===========  ====================================================

该标识在**写入时**固化进每条记忆（``documents.metadata.session_id``、
``memory_atoms.session_id``），而检索按作用域**精确匹配**。因此一旦切换模式，
既有记忆仍带着旧标识，在新作用域下再也检索不到：

1. 检索侧不会把旧作用域纳入候选（``GLOBAL_MEMORY_SCOPE`` 只是普通字符串，
   没有任何"全局即不过滤"的特判）；
2. 自动迁移 ``_migrate_session_data_if_needed()`` 的触发条件恰好是
   ``not session_id.startswith("livingmemory:")``，把 ``global`` / ``user``
   产生的新作用域排除在外，因此永远不会被触发。

结果是：切换配置后"新记忆能跨会话、旧记忆全部失联"，且没有任何补救入口。

本模块提供**显式**迁移能力——由调用方（用户）指定目标作用域，把既有记录
改写过去。刻意不做任何隐式推断：目标要么是预设关键字，要么是调用方给出的
字面作用域字符串。

实现说明
--------
``documents`` 的 metadata 可能缺失、被写坏（非 JSON）或写成非对象根
（``[]`` / 标量）：这类行同样按空作用域纳入迁移，并在写入时归一化为 JSON 对象，
避免"报告已更新、实际未改变"的假象。``memory_atoms`` / ``graph_entries`` 只在
图记忆启用时创建，因此每张表都先用 ``sqlite_master`` 探测，缺表时跳过并记录，
而不是让整个命令失败。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger

# documents 表中的作用域表达式。json_extract 对损坏的 metadata 会直接抛错、
# 对非对象根会静默取不到值，因此先归一化为 JSON 对象（与 bm25_retriever 的
# json_valid 保护保持一致，CASE 保证 json_type 只在 json_valid 为真时才求值）。
_DOC_OBJECT_EXPR = (
    "CASE WHEN json_valid(metadata) "
    "THEN CASE WHEN json_type(metadata) = 'object' THEN metadata ELSE '{}' END "
    "ELSE '{}' END"
)
_DOC_SCOPE_EXPR = f"COALESCE(json_extract({_DOC_OBJECT_EXPR}, '$.session_id'), '')"
# memory_atoms / graph_entries 表中的作用域列（两张表列名相同）
_SCOPE_COLUMN_EXPR = "COALESCE(session_id, '')"

# 参与迁移的三张表：documents 必选，另外两张只在图记忆启用时创建
_TABLE_SCOPE_EXPRS: tuple[tuple[str, str], ...] = (
    ("documents", _DOC_SCOPE_EXPR),
    ("memory_atoms", _SCOPE_COLUMN_EXPR),
    ("graph_entries", _SCOPE_COLUMN_EXPR),
)


@dataclass
class ScopeBreakdown:
    """按作用域统计的存量分布。"""

    documents: dict[str, int] = field(default_factory=dict)
    atoms: dict[str, int] = field(default_factory=dict)
    graph_entries: dict[str, int] = field(default_factory=dict)
    missing_tables: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            sum(self.documents.values())
            + sum(self.atoms.values())
            + sum(self.graph_entries.values())
        )

    @property
    def scopes(self) -> list[str]:
        return sorted(
            set(self.documents) | set(self.atoms) | set(self.graph_entries)
        )


@dataclass
class MigrationPlan:
    """一次迁移的影响面（dry-run 也走这里，因此必须只读）。"""

    target: str
    source: str | None = None
    documents: int = 0
    atoms: int = 0
    graph_entries: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.documents + self.atoms + self.graph_entries

    @property
    def is_noop(self) -> bool:
        return self.total == 0


@dataclass
class MigrationResult:
    """迁移执行结果。"""

    target: str
    documents: int = 0
    atoms: int = 0
    graph_entries: int = 0
    backup_path: str | None = None
    # 提交后复核仍未落到目标作用域的记录数（并发写入时为非 0）
    remaining: int = 0

    @property
    def total(self) -> int:
        return self.documents + self.atoms + self.graph_entries


class ScopeMigrator:
    """把既有记忆的作用域改写到指定目标。

    Args:
        db_connection: 插件主库连接（aiosqlite 风格：``execute`` / ``commit``）。
    """

    def __init__(self, db_connection: Any) -> None:
        if db_connection is None:
            raise ValueError("db_connection 不能为空")
        self._db = db_connection

    async def _existing_tables(self) -> set[str]:
        """探测迁移涉及的表在当前库中是否存在（图记忆关闭时可能缺表）。"""
        names = [name for name, _ in _TABLE_SCOPE_EXPRS]
        placeholders = ", ".join("?" for _ in names)
        cursor = await self._db.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' "
            f"AND name IN ({placeholders})",
            tuple(names),
        )
        return {str(name) for (name,) in await cursor.fetchall()}

    # ------------------------------------------------------------------
    # 只读：盘点与计划
    # ------------------------------------------------------------------

    async def survey(self) -> ScopeBreakdown:
        """盘点当前各作用域的存量（只读），缺表时记录并跳过。"""
        breakdown = ScopeBreakdown()
        existing = await self._existing_tables()

        for table, scope_expr in _TABLE_SCOPE_EXPRS:
            if table not in existing:
                breakdown.missing_tables.append(table)
                continue

            cursor = await self._db.execute(
                f"SELECT {scope_expr} AS scope, COUNT(*) FROM {table} GROUP BY scope"
            )
            counts = {
                str(scope): int(count) for scope, count in await cursor.fetchall()
            }
            if table == "documents":
                breakdown.documents = counts
            elif table == "memory_atoms":
                breakdown.atoms = counts
            else:
                breakdown.graph_entries = counts

        return breakdown

    async def plan(self, target: str, source: str | None = None) -> MigrationPlan:
        """计算影响面，**不写库**。

        Args:
            target: 目标作用域（非空）。
            source: 可选，仅迁移该来源作用域的记录；``None`` 表示"除目标外的全部"。
        """
        target = (target or "").strip()
        if not target:
            raise ValueError("target 不能为空")
        source = (source or "").strip() or None

        plan = MigrationPlan(target=target, source=source)
        existing = await self._existing_tables()

        for table, scope_expr in _TABLE_SCOPE_EXPRS:
            if table not in existing:
                continue

            sql = (
                f"SELECT {scope_expr} AS scope, COUNT(*) FROM {table} "
                f"WHERE {scope_expr} <> ?"
            )
            params: list[Any] = [target]
            if source is not None:
                sql += f" AND {scope_expr} = ?"
                params.append(source)
            sql += " GROUP BY scope"

            cursor = await self._db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            counted = 0
            for scope, count in rows:
                counted += int(count)
                key = str(scope)
                plan.by_source[key] = plan.by_source.get(key, 0) + int(count)

            if table == "documents":
                plan.documents = counted
            elif table == "memory_atoms":
                plan.atoms = counted
            else:
                plan.graph_entries = counted

        return plan

    # ------------------------------------------------------------------
    # 写入：备份与执行
    # ------------------------------------------------------------------

    @staticmethod
    async def backup_to(db_connection: Any, target_path: str | Path) -> Path:
        """用 ``VACUUM INTO`` 生成一致性快照（目标文件必须不存在）。"""
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"备份目标已存在: {path}")
        await db_connection.execute("VACUUM INTO ?", (str(path),))
        return path

    async def execute(
        self, plan: MigrationPlan, *, backup_path: str | Path | None = None
    ) -> MigrationResult:
        """按计划执行迁移。

        幂等性：WHERE 条件包含 ``<> target``，因此重复执行不会重复改写，
        也不会破坏此前记录的 ``old_session_id``。

        事务：三张表的 UPDATE 在同一事务内提交，任一步失败都会 ``rollback``，
        不会把半成品留给共享连接；``VACUUM INTO`` 不能在事务中执行，因此备份前
        先把连接上可能残留的未提交写入落盘。
        """
        result = MigrationResult(target=plan.target)

        if backup_path is not None:
            # 共享连接可能残留其他任务的未提交写入，VACUUM INTO 在事务中会直接报错
            await self._db.commit()
            path = await self.backup_to(self._db, backup_path)
            result.backup_path = str(path)
            logger.info(f"[作用域迁移] 已生成备份: {path}")

        stamp = time.time()
        existing = await self._existing_tables()
        statements: list[tuple[str, str, list[Any]]] = []

        if "documents" in existing:
            doc_where = f"{_DOC_SCOPE_EXPR} <> ?"
            doc_params: list[Any] = [plan.target, stamp, plan.target]
            if plan.source is not None:
                doc_where += f" AND {_DOC_SCOPE_EXPR} = ?"
                doc_params.append(plan.source)
            doc_sql = f"""
                UPDATE documents
                SET metadata = json_set(
                    {_DOC_OBJECT_EXPR},
                    '$.session_id', ?,
                    '$.migrated_at', ?,
                    '$.old_session_id', json_extract({_DOC_OBJECT_EXPR}, '$.session_id')
                )
                WHERE {doc_where}
            """
            statements.append(("documents", doc_sql, doc_params))

        for table in ("memory_atoms", "graph_entries"):
            if table not in existing:
                continue
            where = f"{_SCOPE_COLUMN_EXPR} <> ?"
            params: list[Any] = [plan.target, plan.target]
            if plan.source is not None:
                where += f" AND {_SCOPE_COLUMN_EXPR} = ?"
                params.append(plan.source)
            statements.append(
                (table, f"UPDATE {table} SET session_id = ? WHERE {where}", params)
            )

        counts = {"documents": 0, "memory_atoms": 0, "graph_entries": 0}
        try:
            for table, sql, params in statements:
                cursor = await self._db.execute(sql, tuple(params))
                counts[table] = int(cursor.rowcount or 0)
            await self._db.commit()
        except Exception:
            # 失败必须回滚：否则第一条 UPDATE 会残留在共享连接上，
            # 被后续任何一次无关的 commit 半提交
            await self._db.rollback()
            raise

        result.documents = counts["documents"]
        result.atoms = counts["memory_atoms"]
        result.graph_entries = counts["graph_entries"]

        # 提交后复核：仍有记录未落到目标作用域时（并发写入/异常数据）如实上报
        remaining = await self.plan(plan.target, source=plan.source)
        result.remaining = remaining.total
        if result.remaining:
            logger.warning(
                f"[作用域迁移] 提交后仍有 {result.remaining} 条记录未迁移到 {plan.target}"
            )

        logger.info(
            f"[作用域迁移] 已迁移 documents={result.documents} "
            f"atoms={result.atoms} graph_entries={result.graph_entries} -> {plan.target}"
        )
        return result
