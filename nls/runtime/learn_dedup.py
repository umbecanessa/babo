"""Dedup helpers for safety-net LEARN facts before UI broadcast."""

from __future__ import annotations

import re
from typing import Any, Iterable

_MAX_BROADCAST_KEYS = 300
_MIN_SUBSTRING_DEDUP_LEN = 18
_MIN_TOKEN_OVERLAP = 0.72
_MIN_TOKEN_LEN = 4


def learning_dedup_key(text: str) -> str:
    """Normalize fact text for near-duplicate detection."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = t.strip("\"'")
    return t


def _significant_tokens(key: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9']+", key)
        if len(w) >= _MIN_TOKEN_LEN
    }


def is_near_duplicate(key: str, known: set[str]) -> bool:
    if not key:
        return True
    if key in known:
        return True
    tokens = _significant_tokens(key)
    for existing in known:
        if len(key) >= _MIN_SUBSTRING_DEDUP_LEN and len(existing) >= _MIN_SUBSTRING_DEDUP_LEN:
            if key in existing or existing in key:
                return True
        if len(tokens) >= 3:
            other = _significant_tokens(existing)
            if len(other) >= 3:
                overlap = len(tokens & other) / max(len(tokens), len(other))
                if overlap >= _MIN_TOKEN_OVERLAP:
                    return True
    return False


def collect_known_keys_from_ans(ans: Any | None) -> set[str]:
    """Seed dedup set from current ANS LEARN buffer."""
    keys: set[str] = set()
    if ans is None:
        return keys
    for sig in getattr(ans, "_signal_buffer", []) or []:
        if getattr(sig, "signal_type", "") != "LEARN":
            continue
        raw = getattr(sig, "pipe_fact", None) or getattr(sig, "content", "") or ""
        k = learning_dedup_key(raw)
        if k:
            keys.add(k)
    return keys


def filter_new_learn_facts(
    facts: Iterable[str],
    known_keys: set[str],
) -> list[str]:
    """Return facts not already in known_keys; updates known_keys in place."""
    new_facts: list[str] = []
    for fact in facts:
        key = learning_dedup_key(fact)
        if is_near_duplicate(key, known_keys):
            continue
        known_keys.add(key)
        new_facts.append(fact)
    return new_facts


def remember_broadcast_keys(
    cache: dict[str, None],
    keys: Iterable[str],
    *,
    max_size: int = _MAX_BROADCAST_KEYS,
) -> None:
    """LRU-ish ordered cache of keys already sent to the UI."""
    for key in keys:
        if not key:
            continue
        if key in cache:
            del cache[key]
        cache[key] = None
    while len(cache) > max_size:
        oldest = next(iter(cache))
        del cache[oldest]


def merge_known_from_broadcast_cache(cache: dict[str, None]) -> set[str]:
    return set(cache.keys())
