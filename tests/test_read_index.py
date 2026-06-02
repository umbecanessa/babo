"""Tests for AgentReadIndex (Tier 1 / Tier 2 read cache)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from nls.tools.agent_tools.read_index import AgentReadIndex, tier1_eligible


def test_make_cache_key_stable():
    k1 = AgentReadIndex.make_cache_key("docs/a.md", 1.0, 1000)
    k2 = AgentReadIndex.make_cache_key("docs/a.md", 1.0, 1000)
    k3 = AgentReadIndex.make_cache_key("docs/a.md", 2.0, 1000)
    assert k1 == k2
    assert k1 != k3
    assert k1.startswith("rc_")


def test_record_and_lookup():
    with tempfile.TemporaryDirectory() as td:
        idx = AgentReadIndex(Path(td))
        entry = idx.record_read(
            "docs/prd.md",
            mtime=100.0,
            size=10_000,
            lines=200,
            reader="delegate #4",
            full_text="line\n" * 200,
        )
        found = idx.lookup("docs/prd.md", mtime=100.0, size=10_000)
        assert found is not None
        assert found.cache_key == entry.cache_key


def test_invalidate_path():
    with tempfile.TemporaryDirectory() as td:
        idx = AgentReadIndex(Path(td))
        idx.record_read(
            "src/main.py",
            mtime=1.0,
            size=500,
            lines=10,
            reader="orchestrator",
        )
        assert idx.lookup("src/main.py", mtime=1.0, size=500) is not None
        n = idx.invalidate_path("src/main.py")
        assert n >= 1
        assert idx.lookup("src/main.py", mtime=1.0, size=500) is None


def test_tier2_cached_slice():
    with tempfile.TemporaryDirectory() as td:
        idx = AgentReadIndex(Path(td))
        lines = [f"line {i} " + ("x" * 100) for i in range(1, 101)]
        text = "\n".join(lines)
        idx.record_read(
            "big.txt",
            mtime=5.0,
            size=len(text.encode()),
            lines=100,
            reader="delegate #1",
            full_text=text,
        )
        slice_text = idx.get_cached_slice(
            "big.txt", mtime=5.0, size=len(text.encode()), offset=51, limit=10,
        )
        assert slice_text is not None
        assert "    51|" in slice_text


def test_tier1_eligible_threshold():
    assert not tier1_eligible(1000)
    assert tier1_eligible(3000)


def test_format_cache_hit_includes_recovery():
    with tempfile.TemporaryDirectory() as td:
        idx = AgentReadIndex(Path(td))
        entry = idx.record_read(
            "docs/x.md", mtime=1.0, size=9000, lines=50, reader="delegate #2",
        )
        msg = idx.format_cache_hit(entry, current_lines=50)
        assert "offset=" in msg
        assert "force=true" in msg
        assert entry.cache_key in msg
