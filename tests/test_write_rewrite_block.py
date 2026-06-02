"""Delegate full-rewrite guard on write tool."""

from __future__ import annotations

import pytest

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.tools.agent_tools.write import WriteTool


@pytest.mark.asyncio
async def test_delegate_second_write_blocked_until_file_removed(tmp_path):
    tool = WriteTool(str(tmp_path), block_full_rewrite_after_first=True)
    target = tmp_path / "main.py"

    first = await tool.execute({"path": "main.py", "content": "v1\n"})
    assert not first.is_error

    second = await tool.execute({"path": "main.py", "content": "v2\n"})
    assert second.is_error
    assert "delete_file" in second.content
    assert second.details.get("rewrite_blocked") is True

    engine = BreadcrumbEngine()
    hint = engine.evaluate(
        BreadcrumbContext(
            tool_name="write",
            is_error=True,
            result_details=second.details,
        ),
    )
    assert hint and "delete_file" in hint

    target.unlink()
    third = await tool.execute({"path": "main.py", "content": "v3\n"})
    assert not third.is_error
    assert target.read_text(encoding="utf-8") == "v3\n"
