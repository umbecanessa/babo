"""Tests for learn fact deduplication."""

from nls.runtime.learn_dedup import (
    filter_new_learn_facts,
    is_near_duplicate,
    learning_dedup_key,
    remember_broadcast_keys,
)


def test_learning_dedup_key_normalizes_whitespace_and_case():
    a = learning_dedup_key('  The Agent\'s Name is Babo  ')
    b = learning_dedup_key("the agent's name is babo")
    assert a == b


def test_near_duplicate_substring():
    known = {learning_dedup_key("the project is named ICF Coaching Evaluation Platform")}
    dup = learning_dedup_key('Project name: "ICF Coaching Evaluation Platform"')
    assert is_near_duplicate(dup, known)


def test_filter_new_learn_facts_skips_dupes():
    known = {learning_dedup_key("uses assembly ai for transcription")}
    out = filter_new_learn_facts(
        [
            "Uses Assembly AI for transcription",
            "Deployed on Railway",
        ],
        known,
    )
    assert out == ["Deployed on Railway"]
    assert learning_dedup_key("Deployed on Railway") in known


def test_remember_broadcast_keys_trims_lru():
    cache: dict[str, None] = {}
    remember_broadcast_keys(cache, [f"k{i}" for i in range(5)], max_size=3)
    assert len(cache) == 3
    assert "k0" not in cache
    assert "k4" in cache
