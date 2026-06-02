"""Glob brace expansion on Windows-style patterns."""



from __future__ import annotations



import tempfile

from pathlib import Path



import pytest



from nls.tools.agent_tools.glob import GlobTool, _expand_brace_globs





def test_expand_brace_globs():

    out = _expand_brace_globs("**/*.{jsx,js}")

    assert out == ["**/*.jsx", "**/*.js"]





@pytest.mark.asyncio

async def test_glob_brace_alternates_find_files():

    with tempfile.TemporaryDirectory() as tmp:

        root = Path(tmp)

        (root / "a.jsx").write_text("x", encoding="utf-8")

        (root / "b.js").write_text("y", encoding="utf-8")

        tool = GlobTool(str(root))

        r = await tool.execute({"pattern": "*.{jsx,js}", "path": str(root)})

        assert "a.jsx" in r.content

        assert "b.js" in r.content

        assert "0 file" not in r.content


