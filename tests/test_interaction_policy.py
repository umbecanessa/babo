"""Interaction policy presets, email threads, and channel_inspect summaries."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.channel_inspect import inspect_channel
from nls.runtime.channel_policy_profiles import RUNTIME_CONFIG_KEYS
from nls.runtime.interaction_policy import (
    INTERACTION_PRESET_META_KEY,
    _parse_classification_blob,
    check_email_inbound_policy,
    expand_interaction_preset,
    infer_preset_from_config,
    is_shared_email_inbound,
    normalize_email_address,
    summarize_interaction_mode,
)


def test_expand_owner_plus_shared_discord():
    current = {
        "owner_identity": "wasnaga",
        "dm_policy": "disabled",
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
    patch = expand_interaction_preset(
        "discord-channel",
        "owner_plus_shared",
        owner="wasnaga",
        current=current,
    )
    assert patch["dm_policy"] == "allowlist"
    assert "wasnaga" in patch["allow_from"]
    assert patch["groups"]


def test_expand_shared_only_email():
    patch = expand_interaction_preset(
        "email-channel",
        "shared_only",
        owner=["owner@example.com"],
        current={},
    )
    assert patch["dm_policy"] == "disabled"
    assert patch["thread_policy"] == "owner_initiated"


def test_infer_preset_discord():
    cfg = {
        "dm_policy": "disabled",
        "scoped_channels": {
            "channels": {
                "1": {
                    "id": "1",
                    "effective_enabled": True,
                    "require_mention": True,
                },
            },
        },
    }
    assert infer_preset_from_config("discord-channel", cfg) == "shared_only"


def test_email_shared_thread_detection():
    agent = {"agent@babo.test"}
    headers = {
        "To": "agent@babo.test, colleague@corp.test",
        "Cc": "",
    }
    assert is_shared_email_inbound(headers, agent) is True

    headers_1to1 = {"To": "agent@babo.test", "Cc": ""}
    assert is_shared_email_inbound(headers_1to1, agent) is False

    headers_cc = {"To": "agent@babo.test", "Cc": "other@corp.test"}
    assert is_shared_email_inbound(headers_cc, agent) is True


def test_email_thread_policy_owner_initiated():
    cfg = {
        "dm_policy": "disabled",
        "thread_policy": "owner_initiated",
        "owner_identity": ["owner@example.com"],
        "allow_from": [],
    }
    agent = {"agent@babo.test"}
    headers = {
        "To": "agent@babo.test, colleague@corp.test",
        "Cc": "owner@example.com",
    }
    assert check_email_inbound_policy(cfg, "colleague@corp.test", headers, agent)

    headers_no_owner = {
        "To": "agent@babo.test, stranger@corp.test",
        "Cc": "",
    }
    assert not check_email_inbound_policy(cfg, "stranger@corp.test", headers_no_owner, agent)


def test_email_private_dm_policy():
    cfg = {
        "dm_policy": "allowlist",
        "thread_policy": "disabled",
        "owner_identity": ["owner@example.com"],
        "allow_from": ["owner@example.com"],
    }
    agent = {"agent@babo.test"}
    headers = {"To": "agent@babo.test", "Cc": ""}
    assert check_email_inbound_policy(cfg, "owner@example.com", headers, agent)
    assert not check_email_inbound_policy(cfg, "stranger@corp.test", headers, agent)


def test_summarize_interaction_mode():
    cfg = {
        "dm_policy": "allowlist",
        "owner_identity": "wasnaga",
        "scoped_channels": {
            "channels": {
                "10": {
                    "id": "10",
                    "effective_enabled": True,
                    "require_mention": True,
                },
            },
        },
    }
    text = summarize_interaction_mode("discord-channel", cfg)
    assert "mode=owner_plus_shared" in text
    assert "private=allowlist" in text


def test_expand_trusted_allowlist_email():
    patch = expand_interaction_preset(
        "email-channel",
        "trusted_allowlist",
        owner=["owner@example.com"],
        current={},
    )
    assert patch["dm_policy"] == "allowlist"
    assert patch["thread_policy"] == "allowlist"
    assert patch[INTERACTION_PRESET_META_KEY] == "trusted_allowlist"


def test_infer_uses_stored_preset_meta():
    cfg = {
        INTERACTION_PRESET_META_KEY: "trusted_allowlist",
        "dm_policy": "open",
    }
    assert infer_preset_from_config("email-channel", cfg) == "trusted_allowlist"


def test_parse_classification_blob():
    blob = (
        '{"preset":"owner_plus_shared","owner_ref":"wasnaga",'
        '"confidence":0.91,"needs_confirmation":false,'
        '"user_facing_summary":"Owner DMs plus shared channels."}'
    )
    parsed = _parse_classification_blob(blob)
    assert parsed is not None
    assert parsed.preset == "owner_plus_shared"
    assert parsed.owner_ref == "wasnaga"


def test_expand_includes_runtime_keys_for_discord():
    patch = expand_interaction_preset(
        "discord-channel",
        "shared_only",
        current={
            "scoped_channels": {
                "channels": {
                    "10": {
                        "id": "10",
                        "effective_enabled": True,
                        "require_mention": True,
                    },
                },
            },
        },
    )
    assert "groups" in patch
    assert "dm_policy" in patch
    assert "groups" in RUNTIME_CONFIG_KEYS["discord-channel"]
    assert "scoped_channels" in RUNTIME_CONFIG_KEYS["discord-channel"]


def test_normalize_email_address_display_name():
    assert normalize_email_address("Alice <alice@corp.test>") == "alice@corp.test"
    assert normalize_email_address("bob@corp.test") == "bob@corp.test"


def test_email_policy_with_display_name_sender():
    cfg = {
        "dm_policy": "allowlist",
        "thread_policy": "disabled",
        "owner_identity": ["owner@example.com"],
        "allow_from": ["owner@example.com"],
    }
    agent = {"agent@babo.test"}
    headers = {"To": "agent@babo.test", "Cc": ""}
    assert check_email_inbound_policy(
        cfg, "Owner Name <owner@example.com>", headers, agent,
    )


def test_inspect_includes_interaction(tmp_path: Path):
    agent_id = "agent-ip-1"
    path = tmp_path / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "tok",
                "owner_identity": "owner",
                "dm_policy": "disabled",
                "scoped_channels": {
                    "channels": {
                        "10": {
                            "id": "10",
                            "name": "general",
                            "effective_enabled": True,
                            "require_mention": True,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    text = inspect_channel(tmp_path, agent_id, "discord")
    assert "interaction:" in text
    assert "shared_only" in text or "private=disabled" in text
