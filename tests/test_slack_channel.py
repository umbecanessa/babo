"""Unit tests for Slack and Discord bundled channel adapters."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module(skill_dir: str):
    path = ROOT / "nls" / "skills" / "bundled" / skill_dir / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"{skill_dir}_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


DiscordAdapter = _load_adapter_module("discord-channel").DiscordAdapter
discord_setup_gaps = _load_adapter_module("discord-channel").discord_setup_gaps
SlackAdapter = _load_adapter_module("slack-channel").SlackAdapter


class _Ctx:
    _skills_dir = ROOT / "data" / "skills"

    def load_all_agent_configs(self) -> dict[str, dict]:
        return {}

    def save_config(self, cfg: dict, agent_id: str) -> None:
        pass


def _discord_adapter(cfg: dict[str, Any]) -> DiscordAdapter:
    adapter = DiscordAdapter(global_config=cfg, ctx=_Ctx())
    adapter._agent_configs["agent-1"] = dict(cfg)
    adapter._bot_ids["agent-1"] = "999"
    return adapter


def _slack_adapter(cfg: dict[str, Any]) -> SlackAdapter:
    adapter = SlackAdapter(global_config=cfg, ctx=_Ctx())
    adapter._agent_configs["agent-1"] = dict(cfg)
    return adapter


def test_discord_should_respond_dm_allowlist():
    adapter = _discord_adapter({
        "dm_policy": "allowlist",
        "allow_from": ["111"],
        "scoped_channels": {"channels": {}, "guilds": {}},
        "groups": {"__none__": {"require_mention": True, "allow_from": []}},
    })
    msg = {
        "author": {"id": "111", "username": "owner"},
        "channel_id": "dm1",
        "guild_id": None,
        "content": "hi",
        "mentions": [],
    }
    assert adapter.should_respond(msg, agent_id="agent-1") is True


def test_discord_blocks_unscoped_guild_channel():
    adapter = _discord_adapter({
        "dm_policy": "disabled",
        "scoped_channels": {"channels": {}, "guilds": {}},
        "groups": {"__none__": {"require_mention": True, "allow_from": []}},
    })
    msg = {
        "author": {"id": "222", "username": "user"},
        "channel_id": "555",
        "guild_id": "777",
        "content": "hello",
        "mentions": [{"id": "999"}],
    }
    assert adapter.should_respond(msg, agent_id="agent-1") is False


def test_discord_explain_policy_block_mention_required():
    adapter = _discord_adapter({
        "dm_policy": "disabled",
        "scoped_channels": {
            "channels": {
                "555": {"id": "555", "name": "general", "effective_enabled": True},
            },
            "guilds": {},
        },
        "groups": {"555": {"require_mention": True, "allow_from": ["*"]}},
    })
    msg = {
        "author": {"id": "222", "username": "user"},
        "channel_id": "555",
        "guild_id": "777",
        "content": "hello without mention",
        "mentions": [],
    }
    assert adapter.should_respond(msg, agent_id="agent-1") is False
    assert adapter.explain_policy_block(msg, agent_id="agent-1") == "mention required or sender not allowed"


def test_discord_setup_gaps_after_token_only():
    cfg = {
        "bot_token": "x",
        "enabled": True,
        "dm_policy": "disabled",
        "scoped_channels": {"channels": {}, "guilds": {}},
    }
    gaps = discord_setup_gaps(cfg)
    assert "owner_identity" in gaps
    assert "at least one channel listening in scope" in gaps


def test_slack_normalize_app_mention():
    adapter = _slack_adapter({})
    event = {
        "type": "app_mention",
        "user": "U123",
        "channel": "C456",
        "text": "<@BABO> hello",
    }
    norm = adapter.normalize_event(event, "agent-1")
    assert norm is not None
    assert norm["session_key"] == "slack:channel:C456"
    assert norm["is_mention"] is True


def test_slack_verify_signature():
    adapter = _slack_adapter({"signing_secret": "secret"})
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    base = f"v0:{ts}:{body.decode()}"
    digest = hmac.new(b"secret", base.encode(), hashlib.sha256).hexdigest()
    sig = f"v0={digest}"
    assert adapter.verify_signature("secret", ts, body, sig) is True
