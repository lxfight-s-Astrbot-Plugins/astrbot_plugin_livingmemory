"""Rule-based atom classifier — no additional LLM calls required."""

from __future__ import annotations

import re
import time
from typing import Any

from ..models.memory_atom import AtomType, DecayType, MemoryAtom, compute_ttl


# ---------- classification patterns (Chinese-focused, extensible) ----------

_TIME_INDICATORS = re.compile(
    r"明天|后天|大后天|昨天|前天|今天|下周|下下周|下周[一二三四五六日]|"
    r"本周[一二三四五六日]|上周[一二三四五六日]|"
    r"下个?月|上个?月|明年|后年|去年|前年|"
    r"\d{1,2}月\d{1,2}[日号]|\d{4}年\d{1,2}月|"
    r"上午|下午|晚上|凌晨|早上|中午|傍晚|"
    r"\d{1,2}[点时：:]\d{1,2}"
)

_ACTION_VERBS = re.compile(
    r"开会|讨论|参加|组织|安排|举办|进行|执行|完成|提交|发送|发布|"
    r"去|来|到|做|要|准备|计划|打算"
)

_STATIVE_PATTERNS = re.compile(r"是|有|属于|等于|代表|意味|包含|包括|位于")

_RELATION_PATTERNS = re.compile(
    r"同事|朋友|同学|家人|亲戚|队友|搭档|伙伴|老板|上司|下属|"
    r"合作|合伙|夫妻|情侣|邻居|室友|老乡"
)

_PREFERENCE_PATTERNS = re.compile(
    r"喜欢|讨厌|爱|不爱|偏好|最爱|不喜欢|热衷于|沉迷|"
    r"爱吃|爱喝|喜欢喝|喜欢去|讨厌吃|讨厌去"
)

_PERSON_PATTERNS = re.compile(
    r"(?:我|[你您]|[他她它]们?|[A-Z]\w*|[张李王刘陈杨赵黄周吴徐孙胡朱高何]"
    r"[a-zA-Z一-鿿]{1,3})"
)


def _parse_event_time(text: str) -> float | None:
    """Best-effort extraction of an absolute timestamp from Chinese time expressions.

    Falls back to simple day-offset heuristics when dateparser is unavailable.
    """
    now = time.time()
    day_sec = 86400.0

    mapping: dict[str, float] = {
        "前天": -2 * day_sec, "昨天": -1 * day_sec, "今天": 0,
        "明天": 1 * day_sec, "后天": 2 * day_sec, "大后天": 3 * day_sec,
    }
    for word, offset in mapping.items():
        if word in text:
            return now + offset

    week_offset: dict[str, float] = {}
    for i, day in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天"]):
        week_offset[day] = float(i)

    for day, base in week_offset.items():
        if day in text:
            return now + (base * day_sec)

    # month/day format like "5月30日"
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        import datetime
        month, day = int(m.group(1)), int(m.group(2))
        now_dt = datetime.datetime.fromtimestamp(now)
        target = now_dt.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        if target < now_dt:
            target = target.replace(year=now_dt.year + 1)
        return target.timestamp()

    return None


def classify_atoms(
    key_facts: list[str],
    topics: list[str] | None = None,
    participants: list[str] | None = None,
    parent_importance: float = 0.5,
    session_id: str | None = None,
    persona_id: str | None = None,
) -> list[MemoryAtom]:
    """Classify a list of key_fact strings into MemoryAtom instances.

    Args:
        key_facts: Raw fact strings from LLM extraction.
        topics: Topic labels for entity linking.
        participants: Participant names for entity linking.
        parent_importance: Importance inherited from the parent memory.
        session_id: Session identifier.
        persona_id: Persona identifier.

    Returns:
        A list of MemoryAtom instances, one per fact, with computed TTL and decay.
    """
    entities: list[str] = []
    if topics:
        entities.extend(topics)
    if participants:
        entities.extend(participants)

    atoms: list[MemoryAtom] = []
    for fact in key_facts:
        fact = fact.strip()
        if not fact:
            continue

        atom_type, confidence, event_time = _classify_single(fact)
        ttl, decay = compute_ttl(atom_type, parent_importance, 0, event_time)
        now = time.time()

        atom = MemoryAtom(
            parent_memory_id=0,  # set by caller after insertion
            atom_type=atom_type,
            content=fact,
            entities=list(entities),
            importance=parent_importance,
            confidence=confidence,
            event_time=event_time,
            ttl_days=ttl,
            decay_type=decay,
            expires_at=now + ttl * 86400.0,
            session_id=session_id,
            persona_id=persona_id,
        )
        atoms.append(atom)

    return atoms


def _classify_single(text: str) -> tuple[AtomType, float, float | None]:
    """Classify a single fact string and return (type, confidence, event_time)."""
    has_time = bool(_TIME_INDICATORS.search(text))
    has_action = bool(_ACTION_VERBS.search(text))
    has_stative = bool(_STATIVE_PATTERNS.search(text))
    has_relation = bool(_RELATION_PATTERNS.search(text))
    has_preference = bool(_PREFERENCE_PATTERNS.search(text))

    event_time = _parse_event_time(text) if has_time else None

    # PLANNED: time indicator + action verb → future event
    if has_time and has_action:
        return AtomType.PLANNED, 0.85, event_time

    # PREFERENCE: preference keywords dominate
    if has_preference:
        return AtomType.PREFERENCE, 0.82, None

    # RELATIONAL: person patterns + relation keywords
    if has_relation:
        return AtomType.RELATIONAL, 0.80, None

    # FACTUAL: stative/is-a patterns
    if has_stative:
        return AtomType.FACTUAL, 0.78, None

    # EPISODIC: has action but no time — likely an event description
    if has_action:
        return AtomType.EPISODIC, 0.75, None

    # UNKNOWN fallback
    return AtomType.UNKNOWN, 0.60, None


__all__ = ["classify_atoms", "AtomType", "DecayType", "MemoryAtom"]
