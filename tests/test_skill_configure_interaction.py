"""End-to-end skill_configure + interaction_mode (mock skill loader)."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from nls.runtime.interaction_policy import INTERACTION_PRESET_META_KEY
from nls.tools.agent_tools.skill_configure import SkillConfigureTool


class _Field:
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _discord_schema() -> list[_Field]:
    return [
        _Field(
            key="bot_token", type="secret", required=True, category="connection",
        ),
        _Field(
            key="owner_identity", type="string", required=True, category="identity",
        ),
        _Field(
            key="dm_policy", type="choice", required=True,
            options=["open", "allowlist", "disabled"], category="policy",
        ),
        _Field(key="allow_from", type="list", default=[], category="policy"),
    ]


class _MockSkillContext:
    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id
        self._cfg: dict = {
            "enabled": True,
            "bot_token": "test-token",
            "owner_identity": "wasnaga",
            "dm_policy": "disabled",
            "allow_from": [],
            "scoped_channels": {
                "guilds": {"1": {"id": "1", "name": "Guild"}},
                "channels": {
                    "10": {
                        "id": "10",
                        "name": "general",
                        "guild_id": "1",
                        "enabled_desired": True,
                        "platform_access": True,
                        "effective_enabled": True,
                        "require_mention": True,
                    },
                },
            },
        }
        self.adapter = SimpleNamespace(_agent_configs={agent_id: dict(self._cfg)})

    def load_config(self, agent_id: str | None = None) -> dict:
        return dict(self._cfg)

    def save_config(self, cfg: dict, agent_id: str | None = None) -> None:
        self._cfg = dict(cfg)
        aid = agent_id or self._agent_id
        self.adapter._agent_configs[aid] = dict(cfg)


def _install_mock_server(ctx: _MockSkillContext, skill_name: str = "discord-channel") -> None:
    meta = SimpleNamespace(config_schema=_discord_schema(), name=skill_name)
    skill = SimpleNamespace(name=skill_name, meta=meta, context=ctx)
    skill_loader = SimpleNamespace(skills={skill_name: skill})
    app = SimpleNamespace(
        state=SimpleNamespace(skill_loader=skill_loader, agent_manager=None),
    )
    mod = ModuleType("server.main")
    mod.app = app
    sys.modules["server.main"] = mod


@pytest.mark.asyncio
async def test_skill_configure_applies_interaction_mode_preset():
    agent_id = "agent-e2e-1"
    ctx = _MockSkillContext(agent_id)
    _install_mock_server(ctx)

    tool = SkillConfigureTool(agent_id)
    result = await tool.execute({
        "skill_name": "discord-channel",
        "interaction_mode": "owner_plus_shared",
    })

    assert not result.is_error, result.content
    saved = ctx.load_config()
    assert saved["dm_policy"] == "allowlist"
    assert "wasnaga" in saved["allow_from"]
    assert saved[INTERACTION_PRESET_META_KEY] == "owner_plus_shared"
    assert saved.get("groups")
    assert "Config saved" in result.content


@pytest.mark.asyncio
async def test_skill_configure_rejects_invalid_dm_policy_without_preset():
    agent_id = "agent-e2e-2"
    ctx = _MockSkillContext(agent_id)
    _install_mock_server(ctx)

    tool = SkillConfigureTool(agent_id)
    result = await tool.execute({
        "skill_name": "discord-channel",
        "config": {"dm_policy": "enabled"},
    })

    assert result.is_error
    assert "interaction_mode" in result.content.lower()


@pytest.mark.asyncio
async def test_skill_configure_open_community_requires_confirm():
    agent_id = "agent-e2e-3"
    ctx = _MockSkillContext(agent_id)
    _install_mock_server(ctx)

    tool = SkillConfigureTool(agent_id)
    result = await tool.execute({
        "skill_name": "discord-channel",
        "interaction_mode": "open_community",
    })

    assert result.is_error
    assert "owner_confirm" in result.content.lower()


@pytest.mark.asyncio
async def test_skill_configure_trusted_allowlist_email_schema():
    agent_id = "agent-e2e-4"
    ctx = _MockSkillContext(agent_id)
    email_schema = [
        _Field(key="owner_identity", type="list", required=True, category="identity"),
        _Field(
            key="dm_policy", type="choice", required=True,
            options=["open", "allowlist", "disabled"], category="policy",
        ),
        _Field(key="allow_from", type="list", default=[], category="policy"),
        _Field(
            key="thread_policy", type="choice", default="owner_initiated",
            options=["open", "owner_initiated", "allowlist", "disabled"],
            category="policy",
        ),
    ]
    ctx._cfg = {
        "owner_identity": ["owner@example.com"],
        "dm_policy": "open",
        "allow_from": [],
    }
    meta = SimpleNamespace(config_schema=email_schema, name="email-channel")
    skill = SimpleNamespace(name="email-channel", meta=meta, context=ctx)
    skill_loader = SimpleNamespace(skills={"email-channel": skill})
    app = SimpleNamespace(
        state=SimpleNamespace(skill_loader=skill_loader, agent_manager=None),
    )
    sys.modules["server.main"] = ModuleType("server.main")
    sys.modules["server.main"].app = app

    tool = SkillConfigureTool(agent_id)
    result = await tool.execute({
        "skill_name": "email-channel",
        "interaction_mode": "trusted_allowlist",
    })

    assert not result.is_error, result.content
    saved = ctx.load_config()
    assert saved["thread_policy"] == "allowlist"
    assert saved[INTERACTION_PRESET_META_KEY] == "trusted_allowlist"
