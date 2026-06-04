"""Tests for channel scope reconciliation."""

from nls.skills.channel_scope import (
    apply_desired_channel,
    compile_groups_policy,
    effective_channel_ids,
    merge_observed_channels,
    reconcile_config,
)


def test_merge_observed_auto_enables_new_platform_channel():
    cfg = {"scoped_channels": {"guilds": {}, "channels": {}}}
    observed = [{
        "id": "123",
        "name": "general",
        "guild_id": "999",
        "guild_name": "Team",
        "platform_access": True,
    }]
    scoped = merge_observed_channels(cfg, observed, auto_enable_on_platform_access=True)
    entry = scoped["channels"]["123"]
    assert entry["enabled_desired"] is True
    assert entry["platform_access"] is True
    assert entry["effective_enabled"] is True


def test_effective_requires_both_desired_and_platform():
    cfg = {
        "scoped_channels": {
            "channels": {
                "123": {
                    "id": "123",
                    "enabled_desired": True,
                    "platform_access": False,
                    "effective_enabled": False,
                    "require_mention": True,
                },
            },
        },
    }
    assert effective_channel_ids(cfg) == set()


def test_apply_desired_and_compile_groups():
    cfg = {"scoped_channels": {"guilds": {}, "channels": {}}}
    scoped = apply_desired_channel(cfg, "456", enabled=True, require_mention=True)
    scoped["channels"]["456"]["platform_access"] = True
    scoped["channels"]["456"]["effective_enabled"] = True
    out = reconcile_config({**cfg, "scoped_channels": scoped})
    groups = compile_groups_policy(out)
    assert "456" in groups
    assert groups["456"]["require_mention"] is True


def test_apply_desired_enabling_unknown_channel_assumes_platform_access():
    cfg = {"scoped_channels": {"guilds": {}, "channels": {}}}
    scoped = apply_desired_channel(cfg, "789", enabled=True, require_mention=False)
    entry = scoped["channels"]["789"]
    assert entry["enabled_desired"] is True
    assert entry["platform_access"] is True
    assert entry["effective_enabled"] is True


def test_apply_channels_bulk_config():
    from nls.skills.channel_scope import apply_channels_bulk_config

    cfg = {
        "scoped_channels": {
            "channels": {
                "1": {
                    "id": "1",
                    "name": "general",
                    "enabled_desired": False,
                    "platform_access": True,
                    "effective_enabled": False,
                    "require_mention": True,
                },
            },
        },
    }
    out = apply_channels_bulk_config(cfg, [
        {"id": "1", "enabled": True, "require_mention": False},
        {"id": "2", "enabled": True, "require_mention": True},
    ])
    assert out["scoped_channels"]["channels"]["1"]["enabled_desired"] is True
    assert out["scoped_channels"]["channels"]["2"]["effective_enabled"] is True
    assert "1" in out["groups"]
    assert "2" in out["groups"]


def test_infer_pre_shipped_discord():
    from nls.skills_setup_policy import infer_pre_shipped_channel_skill

    assert infer_pre_shipped_channel_skill("discord bot token") == "discord-channel"
    assert infer_pre_shipped_channel_skill("slack workspace app") == "slack-channel"
