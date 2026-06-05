"""Tests for squad lead member skill configuration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from types import SimpleNamespace

import pytest

from nls.agentic.squad_manager import SquadManager
from nls.agentic.squad_registry import SquadRegistry
from nls.runtime.skill_config_service import (
    SkillConfigOutcome,
    assert_squad_lead_may_configure,
    normalize_config_apply_params,
    patch_has_secret_fields,
    resolve_skill_name,
)


class _Field:
    def __init__(self, key: str, type: str = "string", required: bool = False):
        self.key = key
        self.type = type
        self.required = required
        self.default = None
        self.category = ""
        self.description = ""
        self.options = None


def test_resolve_skill_name_channel_alias():
    assert resolve_skill_name("", "discord") == "discord-channel"
    assert resolve_skill_name("telegram-channel", "") == "telegram-channel"


def test_patch_has_secret_fields():
    schema = [_Field("bot_token", "secret"), _Field("owner_identity")]
    assert patch_has_secret_fields(schema, {"bot_token": "x"})
    assert not patch_has_secret_fields(schema, {"owner_identity": "owner"})


def test_normalize_hoists_interaction_mode_from_skill_config():
    patch, mode, notes = normalize_config_apply_params(
        {"bot_token": "x", "interaction_mode": "shared_only"},
        "",
    )
    assert mode == "shared_only"
    assert "interaction_mode" not in patch
    assert patch["bot_token"] == "x"
    assert notes


def test_normalize_hoists_preset_from_dm_policy():
    patch, mode, notes = normalize_config_apply_params(
        {"dm_policy": "shared_only"},
        "",
    )
    assert mode == "shared_only"
    assert "dm_policy" not in patch
    assert notes


def test_assert_squad_lead_may_configure(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])

    mock_app = MagicMock()
    mock_app.state.squad_manager = SquadManager(
        reg, data_dir=tmp_path, agents_dir=tmp_path / "agents",
    )

    with patch("server.main.app", mock_app):
        assert_squad_lead_may_configure("lead1", "m1")

    with patch("server.main.app", mock_app):
        with pytest.raises(PermissionError):
            assert_squad_lead_may_configure("m1", "lead1")


def test_inspect_member_config_via_handle_action(tmp_path: Path):
    """Regression: skill_name must flow through _pass_kwargs → handle_action."""
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")

    with patch(
        "nls.runtime.skill_config_service.SkillConfigService.inspect",
        return_value=SkillConfigOutcome(True, "fields ok"),
    ):
        with patch(
            "nls.runtime.skill_config_service.assert_squad_lead_may_configure",
        ):
            from nls.tools.agent_tools.squad import _pass_kwargs

            result = mgr.handle_action(
                "lead1",
                "inspect_member_config",
                **_pass_kwargs({
                    "target_agent_id": "m1",
                    "skill_name": "discord-channel",
                }),
            )
    assert result["skill_name"] == "discord-channel"
    assert "fields ok" in result["inspect"]


def test_inspect_member_config_direct(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")
    squad = reg.get_for_agent("lead1")

    with patch(
        "nls.runtime.skill_config_service.SkillConfigService.inspect",
        return_value=SkillConfigOutcome(True, "ok fields"),
    ):
        with patch(
            "nls.runtime.skill_config_service.assert_squad_lead_may_configure",
        ):
            result = mgr._inspect_member_config(
                squad,
                "lead1",
                target_agent_id="m1",
                skill_name="discord-channel",
            )
    assert result["agent_id"] == "m1"
    assert result["skill_name"] == "discord-channel"
    assert "ok fields" in result["inspect"]


@pytest.mark.asyncio
async def test_configure_member_requires_owner_confirm_for_secrets(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")
    squad = reg.get_for_agent("lead1")

    mock_rt = MagicMock()
    with patch(
        "nls.runtime.skill_config_service.ensure_member_runtime",
        new_callable=AsyncMock,
        return_value=mock_rt,
    ):
        with patch(
            "nls.runtime.skill_config_service.assert_squad_lead_may_configure",
        ):
            with patch(
                "nls.runtime.skill_config_service.SkillConfigService.apply",
                new_callable=AsyncMock,
                return_value=SkillConfigOutcome(
                    False,
                    "Secret credential fields require owner confirmation.",
                    True,
                ),
            ):
                with pytest.raises(ValueError, match="owner confirmation"):
                    await mgr._configure_member(
                        squad,
                        "lead1",
                        target_agent_id="m1",
                        skill_name="discord-channel",
                        skill_config={"bot_token": "secret"},
                        owner_confirmed=False,
                    )


@pytest.mark.asyncio
async def test_configure_member_success(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")
    squad = reg.get_for_agent("lead1")

    mock_rt = MagicMock()
    with patch(
        "nls.runtime.skill_config_service.ensure_member_runtime",
        new_callable=AsyncMock,
        return_value=mock_rt,
    ):
        with patch(
            "nls.runtime.skill_config_service.assert_squad_lead_may_configure",
        ):
            with patch(
                "nls.runtime.skill_config_service.SkillConfigService.apply",
                new_callable=AsyncMock,
                return_value=SkillConfigOutcome(True, "Config saved"),
            ):
                with patch(
                    "nls.runtime.skill_config_service.wire_channel_after_config",
                    new_callable=AsyncMock,
                    return_value="Discord connected",
                ):
                    with patch(
                        "nls.runtime.skill_config_service.finalize_discord_member_channels",
                        new_callable=AsyncMock,
                        return_value="2 channel(s) listening",
                    ):
                        result = await mgr._configure_member(
                            squad,
                            "lead1",
                            target_agent_id="m1",
                            skill_name="discord-channel",
                            skill_config={"bot_token": "tok"},
                            owner_confirmed=True,
                        )
    assert result["agent_id"] == "m1"
    assert result["gateway"] == "Discord connected"
    assert result["channel_scope"] == "2 channel(s) listening"


def test_resolve_squad_target_by_display_name(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["mod-id"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")
    squad = reg.get_for_agent("lead1")

    mgr._get_runtime = MagicMock(side_effect=lambda aid: SimpleNamespace(
        agent_name="Mod Agent" if aid == "mod-id" else "Lead",
    ))
    resolved = mgr._resolve_squad_target(squad, "Mod Agent", for_action="brief")
    assert resolved == "mod-id"


def test_resolve_squad_target_lists_roster_on_miss(tmp_path: Path):
    reg = SquadRegistry(tmp_path)
    reg.create(name="Fleet", lead_agent_id="lead1", member_agent_ids=["m1"])
    mgr = SquadManager(reg, data_dir=tmp_path, agents_dir=tmp_path / "agents")
    squad = reg.get_for_agent("lead1")
    mgr._get_runtime = MagicMock(return_value=SimpleNamespace(agent_name="Mod Agent"))

    with pytest.raises(ValueError, match="Squad members"):
        mgr._resolve_squad_target(squad, "Not A Member", for_action="brief")
