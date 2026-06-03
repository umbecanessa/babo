"""Skill loader lifecycle hooks — sync and async startup/shutdown."""

from __future__ import annotations

import pytest

from server.services.skill_loader import SkillLoader


@pytest.mark.asyncio
async def test_invoke_sync_startup_hook():
    called: list[str] = []

    def startup() -> None:
        called.append("sync")

    await SkillLoader._invoke_lifecycle_hook(
        startup, skill_name="test-skill", hook_kind="startup",
    )
    assert called == ["sync"]


@pytest.mark.asyncio
async def test_invoke_async_startup_hook():
    called: list[str] = []

    async def startup() -> None:
        called.append("async")

    await SkillLoader._invoke_lifecycle_hook(
        startup, skill_name="test-skill", hook_kind="startup",
    )
    assert called == ["async"]
