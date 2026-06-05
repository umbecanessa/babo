"""Tests for Discord mentions and squad channel readiness."""

from __future__ import annotations

import importlib

adapter_mod = importlib.import_module("nls.skills.bundled.discord-channel.adapter")
from nls.runtime.discord_squad_readiness import _issue_for_face


def test_extract_discord_user_mention_ids():
    assert adapter_mod.extract_discord_user_mention_ids(
        "ping <@1512360468390346782> and <@!1512360695336009758>",
    ) == ["1512360468390346782", "1512360695336009758"]


def test_discord_send_payload_allowed_mentions():
    payload = adapter_mod.discord_send_payload(
        "hello <@123456789012345678>",
    )
    assert payload["allowed_mentions"] == {"parse": [], "users": ["123456789012345678"]}


def test_issue_when_bot_cannot_view_channel():
    issue = _issue_for_face(
        configured=True,
        api_can_view=False,
        scoped=True,
        listening=True,
        platform_access=True,
        bot_username="Babo Mod",
    )
    assert "cannot view this channel" in issue
    assert "invite" in issue.lower()


def test_issue_when_probe_unavailable():
    issue = _issue_for_face(
        configured=True,
        api_can_view=None,
        scoped=False,
        listening=False,
        platform_access=None,
        bot_username="Babo Mod",
    )
    assert "could not verify" in issue


def test_issue_when_bot_not_in_guild():
    issue = _issue_for_face(
        configured=True,
        api_can_view=None,
        scoped=False,
        listening=False,
        platform_access=None,
        bot_username="Babo Mod",
        in_guild=False,
    )
    assert "not in this Discord server" in issue
    assert "oauth_invite_url" in issue

    issue = _issue_for_face(
        configured=True,
        api_can_view=True,
        scoped=True,
        listening=True,
        platform_access=True,
        bot_username="Babo Mod",
    )
    assert issue == "OK — listening"
