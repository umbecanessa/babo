"""Tests for structured Discord squad playbook."""

from __future__ import annotations

from nls.runtime.discord_squad_playbook import (
    build_playbook,
    grant_access_error_message,
    oauth_invite_url,
    playbook_summary,
)
from nls.runtime.discord_squad_readiness import ChannelFaceStatus


def _face(**kwargs) -> ChannelFaceStatus:
    defaults = dict(
        agent_id="m1",
        name="Mod",
        role="member",
        bot_username="mod_bot",
        bot_id="111111111111111111",
        configured=True,
        api_can_view=False,
        scoped=False,
        listening=False,
        platform_access=None,
        in_guild=False,
        issue="not ready",
    )
    defaults.update(kwargs)
    return ChannelFaceStatus(**defaults)


def test_oauth_invite_url_contains_client_id():
    url = oauth_invite_url("123456789012345678")
    assert "client_id=123456789012345678" in url
    assert "scope=bot" in url.replace("+", " ")


def test_grant_access_error_message_unknown_overwrite():
    msg = grant_access_error_message(404, '{"message": "Unknown Overwrite"}')
    assert "not a member" in msg.lower()
    assert "oauth_invite_url" in msg


def test_build_playbook_oauth_when_not_in_guild():
    playbook = build_playbook(
        [_face(in_guild=False, api_can_view=False)],
        "999",
        guild_id="888",
        in_guild_by_agent={"m1": False},
    )
    assert playbook["all_ready"] is False
    assert len(playbook["oauth_invites"]) == 1
    assert playbook["oauth_invites"][0]["oauth_invite_url"]
    kinds = {s["kind"] for s in playbook["next_steps"]}
    assert "owner_oauth_invite" in kinds


def test_build_playbook_all_ready():
    playbook = build_playbook(
        [_face(
            in_guild=True,
            api_can_view=True,
            scoped=True,
            listening=True,
            issue="OK — listening",
        )],
        "999",
        in_guild_by_agent={"m1": True},
    )
    assert playbook["all_ready"] is True
    assert playbook["next_steps"][0]["kind"] == "test_mentions"


def test_playbook_summary_lists_oauth():
    playbook = build_playbook(
        [_face(in_guild=False)],
        "999",
        in_guild_by_agent={"m1": False},
    )
    text = playbook_summary(playbook)
    assert "Guild invites required" in text
    assert "oauth" in text.lower() or "discord.com" in text
