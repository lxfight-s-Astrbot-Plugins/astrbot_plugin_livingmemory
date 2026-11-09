"""Structured, versioned user profile storage and merge rules.

Profiles are plugin-owned data: one row per ``(scope, persona_id)`` with a
normalised JSON document, a pending-candidate list and append-only history.
The host ``documents`` table is never touched; evidence ids reference memory
document ids only.

Merge rules (see ``apply_updates``):
- ``set`` requires evidence that resolves to a freshly written memory (or a
  memory that still exists) and ``confidence >= min_confidence``; otherwise
  the candidate goes to the pending list.
- ``append`` appends to list sections (preferences/task_preferences) with
  de-duplication.
- ``remove`` requires evidence; direct removal additionally needs
  ``confidence >= min_confidence``, otherwise it becomes a pending candidate.
- Every applied batch bumps ``version`` and writes one history row; history is
  never deleted by updates (only trimmed to ``history_limit``).
"""

from __future__ import annotations

import json
import time
from typing import Any

from astrbot.api import logger

from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float

PROFILE_SECTIONS: tuple[str, ...] = (
    "basic",
    "preferences",
    "status",
    "task_preferences",
)
LIST_PROFILE_SECTIONS: frozenset[str] = frozenset({"preferences", "task_preferences"})
PROFILE_OPERATIONS: tuple[str, ...] = ("set", "append", "remove")

MAX_FIELD_CHARS = 40
MAX_VALUE_CHARS = 200

_FIELD_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. "
)


def empty_profile() -> dict[str, dict[str, Any]]:
    """Return an empty, normalised profile document."""
    return {section: {} for section in PROFILE_SECTIONS}


def normalize_field_name(value: Any) -> str | None:
    """Validate and compact a profile field name.

    Args:
        value: Raw field name from the LLM or API payload.

    Returns:
        A short, charset-restricted field name, or ``None`` when invalid.
    """
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > MAX_FIELD_CHARS:
        return None
    for char in text:
        if char in _FIELD_ALLOWED:
            continue
        if not ("\u4e00" <= char <= "\u9fff"):
            return None
    return text


def normalize_value(value: Any) -> str | None:
    """Compact a scalar profile value to at most ``MAX_VALUE_CHARS`` chars."""
    if isinstance(value, (int, float, bool)) or value is None:
        text = str(value) if value is not None else ""
    else:
        text = str(value)
    text = " ".join(text.split()).strip()
    if not text:
        return None
    if len(text) > MAX_VALUE_CHARS:
        text = text[: MAX_VALUE_CHARS - 1].rstrip() + "…"
    return text


def normalize_profile_json(value: Any) -> dict[str, dict[str, Any]]:
    """Normalise an arbitrary profile document into the supported shape.

    Unknown sections/fields and empty values are dropped so manual edits and
    LLM output can never poison the stored schema.

    Args:
        value: Raw profile document (usually ``{section: {field: value}}``).

    Returns:
        Normalised profile document.
    """
    profile = empty_profile()
    if not isinstance(value, dict):
        return profile
    for section in PROFILE_SECTIONS:
        raw_section = value.get(section)
        if not isinstance(raw_section, dict):
            continue
        for raw_field, raw_value in raw_section.items():
            field = normalize_field_name(raw_field)
            if field is None:
                continue
            if section in LIST_PROFILE_SECTIONS:
                items: list[str] = []
                raw_items = raw_value if isinstance(raw_value, list) else [raw_value]
                for item in raw_items:
                    text = normalize_value(item)
                    if text and text not in items:
                        items.append(text)
                if items:
                    profile[section][field] = items
            else:
                text = normalize_value(raw_value)
                if text:
                    profile[section][field] = text
    return profile


def normalize_profile_updates(updates: Any) -> list[dict[str, Any]]:
    """Normalise LLM-provided profile updates.

    Args:
        updates: Raw ``profile_updates`` value from the summary JSON.

    Returns:
        Valid updates with section/field/value/operation/confidence/evidence;
        invalid entries are dropped silently.
    """
    if not isinstance(updates, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in updates:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "").strip().casefold()
        if section not in PROFILE_SECTIONS:
            continue
        operation = str(raw.get("operation") or "set").strip().casefold()
        if operation not in PROFILE_OPERATIONS:
            continue
        if operation in {"append", "remove"} and section not in LIST_PROFILE_SECTIONS:
            continue
        field = normalize_field_name(raw.get("field"))
        value = normalize_value(raw.get("value"))
        if field is None or value is None:
            continue
        evidence: list[int] = []
        raw_evidence = raw.get("evidence")
        if isinstance(raw_evidence, (list, tuple)):
            for item in raw_evidence:
                if isinstance(item, bool):
                    continue
                try:
                    parsed = int(item)
                except (TypeError, ValueError):
                    continue
                if parsed > 0 and parsed not in evidence:
                    evidence.append(parsed)
        confidence = clamp_float(raw.get("confidence"), default=0.5)
        normalized.append(
            {
                "section": section,
                "field": field,
                "value": value,
                "operation": operation,
                "evidence": evidence,
                "confidence": confidence,
            }
        )
    return normalized


class ProfileManager:
    """Manage ``memory_profiles`` rows and their append-only history."""

    def __init__(self, engine: Any, config: dict[str, Any] | None = None):
        """Bind the manager to a ``MemoryEngine`` instance.

        Args:
            engine: Memory engine providing ``db_connection`` and
                ``_write_transaction``.
            config: Engine runtime config (flattened ``profile_*`` keys).
        """
        self.engine = engine
        self.config = config or {}

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("profile_enabled", True))

    @property
    def max_chars(self) -> int:
        return int(max(50, min(2000, self.config.get("profile_max_chars", 300))))

    @property
    def min_confidence(self) -> float:
        return clamp_float(self.config.get("profile_min_confidence"), default=0.6)

    @property
    def history_limit(self) -> int:
        return int(max(1, min(200, self.config.get("profile_history_limit", 20))))

    @property
    def max_pending(self) -> int:
        return int(max(1, min(500, self.config.get("profile_max_pending", 50))))

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def get_profile(
        self, scope: str | None, persona_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return one profile row by scope/persona, or ``None``."""
        scope_text = str(scope or "").strip()
        if not scope_text or self.engine.db_connection is None:
            return None
        cursor = await self.engine.db_connection.execute(
            "SELECT * FROM memory_profiles "
            "WHERE scope = ? AND COALESCE(persona_id, '') = ?",
            (scope_text, str(persona_id or "")),
        )
        row = await cursor.fetchone()
        return self._row_to_profile(row) if row else None

    async def get_profile_by_id(self, profile_id: int) -> dict[str, Any] | None:
        """Return one profile row by primary key, or ``None``."""
        if self.engine.db_connection is None:
            return None
        cursor = await self.engine.db_connection.execute(
            "SELECT * FROM memory_profiles WHERE id = ?", (int(profile_id),)
        )
        row = await cursor.fetchone()
        return self._row_to_profile(row) if row else None

    async def list_profiles(
        self, scope: str | None = None, persona_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List profiles, optionally filtered by scope/persona."""
        if self.engine.db_connection is None:
            return []
        conditions: list[str] = []
        params: list[Any] = []
        scope_text = str(scope or "").strip()
        if scope_text:
            conditions.append("scope = ?")
            params.append(scope_text)
        if persona_id is not None:
            conditions.append("COALESCE(persona_id, '') = ?")
            params.append(str(persona_id))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self.engine.db_connection.execute(
            f"SELECT * FROM memory_profiles {where} ORDER BY updated_at DESC, id DESC",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_profile(row) for row in rows]

    async def get_history(
        self, profile_id: int, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return newest-first history rows for one profile."""
        if self.engine.db_connection is None:
            return []
        limit_value = min(200, max(1, int(limit or self.history_limit)))
        cursor = await self.engine.db_connection.execute(
            "SELECT * FROM memory_profile_history WHERE profile_id = ? "
            "ORDER BY version DESC, id DESC LIMIT ?",
            (int(profile_id), limit_value),
        )
        rows = await cursor.fetchall()
        return [self._row_to_history(row) for row in rows]

    async def list_pending(
        self, scope: str | None, persona_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return the profile row's pending candidates (with indexes)."""
        profile = await self.get_profile(scope, persona_id)
        if profile is None:
            return None
        pending = profile.get("pending") or []
        return {
            "id": profile["id"],
            "scope": profile["scope"],
            "persona_id": profile["persona_id"],
            "version": profile["version"],
            "updated_at": profile["updated_at"],
            "pending": [
                {"index": index, **candidate} for index, candidate in enumerate(pending)
            ],
        }

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def apply_updates(
        self,
        scope: str | None,
        persona_id: str | None,
        updates: list[dict[str, Any]] | None,
        evidence_memory_ids: list[int] | None = None,
        source: str = "reflection",
    ) -> dict[str, Any]:
        """Apply one summary's profile updates with evidence/confidence gates.

        Args:
            scope: Resolved memory scope.
            persona_id: Persona id (``None`` when persona filtering is off).
            updates: Raw or already-normalised profile updates.
            evidence_memory_ids: Memories written by this summary; empty
                evidence in a candidate is interpreted as this batch.
            source: History source label.

        Returns:
            Merge statistics: ``applied``/``pending`` count and ``version``.
        """
        scope_text = str(scope or "").strip()
        if not scope_text:
            return {"applied": 0, "pending": 0, "version": None}
        batch_ids = [int(item) for item in (evidence_memory_ids or []) if int(item) > 0]
        normalized = normalize_profile_updates(updates)
        if not self.enabled or not normalized:
            return {"applied": 0, "pending": 0, "version": None}

        requested_evidence: set[int] = set(batch_ids)
        for update in normalized:
            requested_evidence.update(update["evidence"])
        valid_evidence = set(batch_ids) | await self._existing_memory_ids(
            requested_evidence
        )

        async with self.engine._write_transaction() as connection:
            row = await self._get_or_create_row(connection, scope_text, persona_id)
            profile = self._parse_profile_json(row["profile_json"])
            pending = self._parse_pending(row["pending_json"])
            version = int(row["version"] or 0)
            changed_fields: list[str] = []
            applied_evidence: set[int] = set()

            for update in self._merge_batch_updates(normalized):
                evidence = set(update["evidence"]) or set(batch_ids)
                evidence_ok = bool(evidence) and evidence <= valid_evidence
                confidence_ok = update["confidence"] >= self.min_confidence
                if update["operation"] == "set":
                    if evidence_ok and confidence_ok:
                        if self._apply_set(profile, update):
                            changed_fields.append(
                                f"{update['section']}.{update['field']}"
                            )
                        pending = self._drop_pending(
                            pending, update["section"], update["field"]
                        )
                        applied_evidence.update(evidence)
                    else:
                        pending = self._add_pending(
                            pending,
                            update,
                            evidence,
                            source,
                        )
                elif update["operation"] == "append":
                    if self._apply_append(profile, update):
                        changed_fields.append(f"{update['section']}.{update['field']}")
                        applied_evidence.update(evidence)
                else:  # remove
                    if evidence_ok and confidence_ok:
                        if self._apply_remove(profile, update):
                            changed_fields.append(
                                f"{update['section']}.{update['field']}"
                            )
                        pending = self._drop_pending(
                            pending, update["section"], update["field"]
                        )
                        applied_evidence.update(evidence)
                    else:
                        pending = self._add_pending(
                            pending,
                            update,
                            evidence,
                            source,
                        )

            if changed_fields:
                version += 1
                await self._write_profile_row(
                    connection,
                    row["id"],
                    profile,
                    pending,
                    version,
                )
                await self._append_history(
                    connection,
                    row["id"],
                    version,
                    profile,
                    changed_fields,
                    applied_evidence,
                    source,
                )
            elif pending != self._parse_pending(row["pending_json"]):
                await self._write_pending_only(connection, row["id"], pending)
                version = int(row["version"] or 0)
            else:
                version = int(row["version"] or 0)

            logger.info(
                "[Profile] 档案更新: scope=%s persona=%s applied=%d pending=%d version=%d",
                scope_text,
                persona_id,
                len(changed_fields),
                len(pending),
                version,
            )
            return {
                "applied": len(changed_fields),
                "pending": len(pending),
                "version": version,
            }

    async def update_profile_manual(
        self,
        profile_id: int,
        *,
        profile_json: Any = None,
        patch: Any = None,
        source: str = "manual",
    ) -> dict[str, Any] | None:
        """Replace or patch a profile as an explicit user edit.

        Args:
            profile_id: Profile primary key.
            profile_json: Full replacement document.
            patch: Nested ``{section: {field: value}}`` patch; ``None``
                removes the field.
            source: History source label (``manual`` by default).

        Returns:
            Updated profile row, or ``None`` when the row does not exist.

        Raises:
            ValueError: When neither ``profile_json`` nor ``patch`` is given.
        """
        if profile_json is None and patch is None:
            raise ValueError("profile_json or patch is required")

        async with self.engine._write_transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM memory_profiles WHERE id = ?", (int(profile_id),)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            current = self._parse_profile_json(row["profile_json"])
            pending = self._parse_pending(row["pending_json"])
            if profile_json is not None:
                updated = normalize_profile_json(profile_json)
            else:
                updated = _apply_patch(current, patch)
            changed_fields = _diff_profile(current, updated)
            version = int(row["version"] or 0)
            if changed_fields:
                version += 1
                await self._write_profile_row(
                    connection, row["id"], updated, pending, version
                )
                await self._append_history(
                    connection,
                    row["id"],
                    version,
                    updated,
                    changed_fields,
                    set(),
                    source,
                )
            cursor = await connection.execute(
                "SELECT * FROM memory_profiles WHERE id = ?", (int(profile_id),)
            )
            return self._row_to_profile(await cursor.fetchone())

    async def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile and its history (explicit audit trade-off).

        Args:
            profile_id: Profile primary key.

        Returns:
            ``True`` when a row was deleted.
        """
        async with self.engine._write_transaction() as connection:
            cursor = await connection.execute(
                "SELECT id FROM memory_profiles WHERE id = ?", (int(profile_id),)
            )
            if not await cursor.fetchone():
                return False
            await connection.execute(
                "DELETE FROM memory_profile_history WHERE profile_id = ?",
                (int(profile_id),),
            )
            await connection.execute(
                "DELETE FROM memory_profiles WHERE id = ?", (int(profile_id),)
            )
            return True

    async def resolve_pending(
        self,
        scope: str | None,
        persona_id: str | None,
        index: int,
        *,
        accept: bool,
    ) -> dict[str, Any] | None:
        """Accept (apply) or reject one pending candidate.

        Args:
            scope: Resolved memory scope.
            persona_id: Persona id.
            index: Zero-based pending candidate index.
            accept: ``True`` applies the candidate, ``False`` discards it.

        Returns:
            Updated profile row, or ``None`` when profile/index is missing.

        Raises:
            ValueError: When ``index`` is out of range.
        """
        scope_text = str(scope or "").strip()
        if not scope_text:
            return None
        async with self.engine._write_transaction() as connection:
            row = await self._get_existing_row(connection, scope_text, persona_id)
            if row is None:
                return None
            profile = self._parse_profile_json(row["profile_json"])
            pending = self._parse_pending(row["pending_json"])
            try:
                candidate = pending.pop(int(index))
            except (IndexError, TypeError, ValueError) as exc:
                raise ValueError("pending index out of range") from exc

            changed_fields: list[str] = []
            version = int(row["version"] or 0)
            if accept:
                update = {
                    "section": candidate["section"],
                    "field": candidate["field"],
                    "value": candidate["value"],
                    "operation": candidate["operation"],
                    "evidence": list(candidate.get("evidence") or []),
                    "confidence": float(candidate.get("confidence") or 1.0),
                }
                if update["operation"] == "remove":
                    changed = self._apply_remove(profile, update)
                elif update["operation"] == "append":
                    changed = self._apply_append(profile, update)
                else:
                    changed = self._apply_set(profile, update)
                if changed:
                    changed_fields.append(f"{update['section']}.{update['field']}")
                version += 1
                await self._write_profile_row(
                    connection, row["id"], profile, pending, version
                )
                await self._append_history(
                    connection,
                    row["id"],
                    version,
                    profile,
                    changed_fields,
                    set(update["evidence"]),
                    "pending_accept",
                )
            else:
                await self._write_pending_only(connection, row["id"], pending)

            cursor = await connection.execute(
                "SELECT * FROM memory_profiles WHERE id = ?", (row["id"],)
            )
            return self._row_to_profile(await cursor.fetchone())

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    async def _existing_memory_ids(self, ids: set[int]) -> set[int]:
        if not ids or self.engine.db_connection is None:
            return set()
        placeholders = ",".join("?" for _ in ids)
        try:
            cursor = await self.engine.db_connection.execute(
                f"SELECT id FROM documents WHERE id IN ({placeholders})",
                tuple(sorted(ids)),
            )
            rows = await cursor.fetchall()
        except Exception:
            logger.warning("[Profile] 校验证据记忆失败", exc_info=True)
            return set()
        return {int(row[0]) for row in rows}

    async def _get_existing_row(
        self, connection: Any, scope: str, persona_id: str | None
    ):
        cursor = await connection.execute(
            "SELECT * FROM memory_profiles "
            "WHERE scope = ? AND COALESCE(persona_id, '') = ?",
            (scope, str(persona_id or "")),
        )
        return await cursor.fetchone()

    async def _get_or_create_row(
        self, connection: Any, scope: str, persona_id: str | None
    ):
        row = await self._get_existing_row(connection, scope, persona_id)
        if row is not None:
            return row
        now = time.time()
        await connection.execute(
            "INSERT INTO memory_profiles "
            "(scope, persona_id, profile_json, pending_json, version, "
            "created_at, updated_at) VALUES (?, ?, ?, '[]', 0, ?, ?)",
            (
                scope,
                persona_id,
                json.dumps(empty_profile(), ensure_ascii=False),
                now,
                now,
            ),
        )
        row = await self._get_existing_row(connection, scope, persona_id)
        if row is None:  # pragma: no cover - insert/read cannot drift
            raise RuntimeError("profile row insert failed")
        return row

    async def _write_profile_row(
        self,
        connection: Any,
        profile_id: int,
        profile: dict[str, Any],
        pending: list[dict[str, Any]],
        version: int,
    ) -> None:
        await connection.execute(
            "UPDATE memory_profiles SET profile_json = ?, pending_json = ?, "
            "version = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(profile, ensure_ascii=False),
                json.dumps(pending, ensure_ascii=False),
                int(version),
                time.time(),
                int(profile_id),
            ),
        )

    async def _write_pending_only(
        self, connection: Any, profile_id: int, pending: list[dict[str, Any]]
    ) -> None:
        await connection.execute(
            "UPDATE memory_profiles SET pending_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(pending, ensure_ascii=False), time.time(), int(profile_id)),
        )

    async def _append_history(
        self,
        connection: Any,
        profile_id: int,
        version: int,
        profile: dict[str, Any],
        changed_fields: list[str],
        evidence_memory_ids: set[int],
        source: str,
    ) -> None:
        await connection.execute(
            "INSERT INTO memory_profile_history "
            "(profile_id, version, profile_json, changed_fields, "
            "evidence_memory_ids, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                int(profile_id),
                int(version),
                json.dumps(profile, ensure_ascii=False),
                json.dumps(sorted(set(changed_fields)), ensure_ascii=False),
                json.dumps(sorted(evidence_memory_ids), ensure_ascii=False),
                str(source or "reflection"),
                time.time(),
            ),
        )
        await connection.execute(
            "DELETE FROM memory_profile_history WHERE profile_id = ? AND id NOT IN "
            "(SELECT id FROM memory_profile_history WHERE profile_id = ? "
            "ORDER BY version DESC, id DESC LIMIT ?)",
            (int(profile_id), int(profile_id), self.history_limit),
        )

    @staticmethod
    def _parse_profile_json(raw: Any) -> dict[str, dict[str, Any]]:
        data = safe_json_dict(raw)
        return normalize_profile_json(data)

    @staticmethod
    def _parse_pending(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []
        else:
            data = raw
        if not isinstance(data, list):
            return []
        return [dict(item) for item in data if isinstance(item, dict)]

    @staticmethod
    def _merge_batch_updates(
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve same-field conflicts inside one batch.

        ``set`` keeps the highest-confidence candidate (later wins on ties);
        ``append``/``remove`` are applied independently in order.
        """
        merged: list[dict[str, Any]] = []
        set_index: dict[tuple[str, str], int] = {}
        for update in updates:
            key = (update["section"], update["field"])
            if update["operation"] == "set" and key in set_index:
                position = set_index[key]
                current = merged[position]
                if update["confidence"] >= current["confidence"]:
                    merged[position] = update
                continue
            if update["operation"] == "set":
                set_index[key] = len(merged)
            merged.append(update)
        return merged

    @staticmethod
    def _apply_set(profile: dict[str, dict[str, Any]], update: dict[str, Any]) -> bool:
        section = update["section"]
        field = update["field"]
        value = update["value"]
        if section in LIST_PROFILE_SECTIONS:
            items = [value]
        else:
            items = value
        if profile[section].get(field) == items:
            return False
        profile[section][field] = items
        return True

    @staticmethod
    def _apply_append(
        profile: dict[str, dict[str, Any]], update: dict[str, Any]
    ) -> bool:
        section = update["section"]
        if section not in LIST_PROFILE_SECTIONS:
            return False
        field = update["field"]
        value = update["value"]
        current = profile[section].get(field)
        items = list(current) if isinstance(current, list) else []
        if value in items:
            return False
        items.append(value)
        profile[section][field] = items
        return True

    @staticmethod
    def _apply_remove(
        profile: dict[str, dict[str, Any]], update: dict[str, Any]
    ) -> bool:
        section = update["section"]
        field = update["field"]
        value = update["value"]
        current = profile[section].get(field)
        if section in LIST_PROFILE_SECTIONS:
            items = list(current) if isinstance(current, list) else []
            if value not in items:
                return False
            items.remove(value)
            if items:
                profile[section][field] = items
            else:
                profile[section].pop(field, None)
            return True
        if current != value:
            return False
        profile[section].pop(field, None)
        return True

    def _add_pending(
        self,
        pending: list[dict[str, Any]],
        update: dict[str, Any],
        evidence: set[int],
        source: str,
    ) -> list[dict[str, Any]]:
        candidate = {
            "section": update["section"],
            "field": update["field"],
            "value": update["value"],
            "operation": update["operation"],
            "evidence": sorted(evidence),
            "confidence": round(float(update["confidence"]), 4),
            "source": str(source or "reflection"),
            "created_at": time.time(),
        }
        replaced = False
        next_pending: list[dict[str, Any]] = []
        for existing in pending:
            if (
                existing.get("section") == candidate["section"]
                and existing.get("field") == candidate["field"]
            ):
                if float(candidate["confidence"]) >= float(
                    existing.get("confidence") or 0.0
                ):
                    next_pending.append(candidate)
                else:
                    next_pending.append(existing)
                replaced = True
                continue
            next_pending.append(existing)
        if not replaced:
            next_pending.append(candidate)
        return next_pending[-self.max_pending :]

    @staticmethod
    def _drop_pending(
        pending: list[dict[str, Any]], section: str, field: str
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in pending
            if not (item.get("section") == section and item.get("field") == field)
        ]

    @staticmethod
    def _row_to_profile(row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "scope": str(row["scope"]),
            "persona_id": row["persona_id"],
            "profile": ProfileManager._parse_profile_json(row["profile_json"]),
            "pending": ProfileManager._parse_pending(row["pending_json"]),
            "version": int(row["version"] or 0),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        }

    @staticmethod
    def _row_to_history(row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "profile_id": int(row["profile_id"]),
            "version": int(row["version"] or 0),
            "profile": ProfileManager._parse_profile_json(row["profile_json"]),
            "changed_fields": _parse_json_list(row["changed_fields"]),
            "evidence_memory_ids": [
                int(item) for item in _parse_json_list(row["evidence_memory_ids"])
            ],
            "source": str(row["source"] or ""),
            "created_at": float(row["created_at"] or 0.0),
        }


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return data if isinstance(data, list) else []
    return []


def _diff_profile(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[str]:
    """Return ``section.field`` names whose value changed."""
    changed: list[str] = []
    for section in PROFILE_SECTIONS:
        before_fields = before.get(section) or {}
        after_fields = after.get(section) or {}
        for field in set(before_fields) | set(after_fields):
            if before_fields.get(field) != after_fields.get(field):
                changed.append(f"{section}.{field}")
    return sorted(changed)


def _apply_patch(
    current: dict[str, dict[str, Any]], patch: Any
) -> dict[str, dict[str, Any]]:
    """Apply a nested or dotted field patch onto a normalised profile."""
    updated = normalize_profile_json(current)
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    for raw_key, raw_value in patch.items():
        key = str(raw_key)
        if "." in key:
            section, _, field = key.partition(".")
            section_value: Any = {field: raw_value}
        else:
            section = key
            section_value = raw_value
        if section not in PROFILE_SECTIONS or not isinstance(section_value, dict):
            continue
        for field, value in section_value.items():
            field_name = normalize_field_name(field)
            if field_name is None:
                continue
            if value is None:
                updated[section].pop(field_name, None)
                continue
            normalized = normalize_profile_json({section: {field_name: value}})
            if normalized[section]:
                updated[section][field_name] = normalized[section][field_name]
            else:
                updated[section].pop(field_name, None)
    return updated


__all__ = [
    "LIST_PROFILE_SECTIONS",
    "PROFILE_OPERATIONS",
    "PROFILE_SECTIONS",
    "ProfileManager",
    "empty_profile",
    "normalize_field_name",
    "normalize_profile_json",
    "normalize_profile_updates",
    "normalize_value",
]
