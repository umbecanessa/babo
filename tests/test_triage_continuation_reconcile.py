"""Triage continuation reconcile: credential paste → configure bundled, not rebuild."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nls.agentic.goals import TurnTriage
from nls.agentic.profile_guard_policy import (
    boost_triage_for_work_continuation,
    looks_like_credential_continuation_turn,
    reconcile_triage_continuation_phase,
    wm_get_tactical_goal_strings,
)

_DISCORD_TOKEN = (
    "MTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMg."
    "GAbCdEf.AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
)

_TELEGRAM_TOKEN = (
    "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
)


class _MockWM:
    def __init__(
        self,
        *,
        tactical_goals: list[str] | None = None,
        active_teams: list[Any] | None = None,
    ):
        self._tactical_goals = tactical_goals or []
        self._active_teams = active_teams or []

    def get_goals(self):
        return [
            SimpleNamespace(level="tactical", content=g)
            for g in self._tactical_goals
        ]

    def orch_get_active_teams(self):
        return self._active_teams


def test_looks_like_credential_after_assistant_asked():
    history = [
        {"role": "user", "content": "Set up Discord for my server"},
        {
            "role": "assistant",
            "content": "Please paste your Discord bot token when ready.",
        },
    ]
    assert looks_like_credential_continuation_turn(_DISCORD_TOKEN, history=history)


def test_reconcile_telegram_token_uses_configure_bundled():
    triage = TurnTriage(
        intent="TASK_THINK",
        profile="solo_structured",
        thinking=True,
        goals=[],
        hints=[],
    )
    history = [
        {"role": "user", "content": "Set up Telegram for my bot"},
        {"role": "assistant", "content": "Paste your Telegram bot token"},
    ]
    reconcile_triage_continuation_phase(
        triage, _TELEGRAM_TOKEN, history=history,
    )
    lowered = {h.lower() for h in triage.hints}
    assert "setup:configure_bundled" in lowered
    assert "telegram-channel" in " ".join(triage.goals).lower()


def test_reconcile_discord_token_uses_configure_bundled():
    triage = TurnTriage(
        intent="TASK_THINK",
        profile="solo_structured",
        thinking=True,
        goals=["Scaffold discord-channel"],
        hints=["setup:native_skill"],
    )
    history = [
        {"role": "user", "content": "Connect Discord to my Babo agent"},
        {"role": "assistant", "content": "Paste your Discord bot token"},
    ]
    reconcile_triage_continuation_phase(
        triage, _DISCORD_TOKEN, history=history,
    )
    lowered = {h.lower() for h in triage.hints}
    assert "setup:configure_bundled" in lowered
    assert "discord-channel" in " ".join(triage.goals).lower()


def test_reconcile_strips_native_skill_on_telegram_token_paste():
    triage = TurnTriage(
        intent="TASK_THINK",
        profile="solo_structured",
        thinking=True,
        goals=["Configure telegram bot"],
        hints=["setup:native_skill", "setup:instruction_skill"],
    )
    history = [
        {"role": "user", "content": "Set up telegram bot"},
        {"role": "assistant", "content": "Paste your bot token"},
    ]
    reconcile_triage_continuation_phase(
        triage, _TELEGRAM_TOKEN, history=history,
    )
    lowered = {h.lower() for h in triage.hints}
    assert "setup:native_skill" not in lowered
    assert "setup:instruction_skill" not in lowered
    assert "setup:configure_bundled" in lowered
    assert "telegram-channel" in " ".join(triage.goals).lower()


def test_boost_telegram_token_adds_configure_bundled():
    triage = TurnTriage(
        intent="CHAT_NOTHINK",
        profile="conversational",
        thinking=False,
    )
    history = [
        {"role": "user", "content": "Set up telegram bot"},
        {"role": "assistant", "content": "Send me your bot token"},
    ]
    boost_triage_for_work_continuation(triage, _TELEGRAM_TOKEN, history=history)
    assert "setup:configure_bundled" in {h.lower() for h in triage.hints}


def test_boost_discord_token_adds_configure_bundled():
    triage = TurnTriage(
        intent="CHAT_NOTHINK",
        profile="conversational",
        thinking=False,
    )
    history = [
        {"role": "user", "content": "Connect Discord to my Babo agent"},
        {"role": "assistant", "content": "Send me your bot token"},
    ]
    boost_triage_for_work_continuation(triage, _DISCORD_TOKEN, history=history)
    lowered = {h.lower() for h in triage.hints}
    assert "setup:configure_bundled" in lowered


def test_reconcile_post_restart_discord_continuation():
    triage = TurnTriage(
        intent="CHAT_THINK",
        profile="conversational",
        thinking=True,
        goals=[],
        hints=[],
    )
    history = [
        {"role": "user", "content": "Connect Discord bot for Babo project"},
        {
            "role": "assistant",
            "content": "Skill review created (id: abc). discord-channel.",
        },
    ]
    reconcile_triage_continuation_phase(
        triage, "ok server restarted", history=history,
    )
    assert triage.profile == "solo_structured"
    assert triage.intent == "TASK_THINK"
    assert triage.goals
    assert "discord" in triage.goals[0].lower()


def test_reconcile_post_restart_uses_wm_tactical_goals():
    wm = _MockWM(tactical_goals=[
        "Finish discord-channel skill_configure",
        "Verify Discord gateway listener",
    ])
    triage = TurnTriage(
        intent="CHAT_THINK",
        profile="conversational",
        thinking=True,
        goals=[],
        hints=[],
    )
    history = [
        {"role": "user", "content": "Connect Discord bot for Babo project"},
        {"role": "assistant", "content": "Skill review created. discord-channel."},
    ]
    reconcile_triage_continuation_phase(
        triage, "ok server restarted", history=history, working_memory=wm,
    )
    assert triage.profile == "solo_structured"
    assert triage.goals == wm_get_tactical_goal_strings(wm)


def test_reconcile_post_restart_preserves_orchestrated_profile():
    wm = _MockWM(
        tactical_goals=["Monitor delegate wave for discord setup"],
        active_teams=[SimpleNamespace(team_id="t1", status="running")],
    )
    triage = TurnTriage(
        intent="CHAT_THINK",
        profile="orchestrated",
        thinking=True,
        goals=[],
        hints=[],
    )
    history = [
        {"role": "user", "content": "Connect Discord bot for Babo project"},
        {"role": "assistant", "content": "Delegated discord-channel setup."},
    ]
    reconcile_triage_continuation_phase(
        triage, "server restarted successfully", history=history, working_memory=wm,
    )
    assert triage.profile == "orchestrated"
    assert triage.intent == "TASK_THINK"
    assert "discord" in triage.goals[0].lower()


def test_reconcile_post_restart_telegram_uses_configure_bundled():
    triage = TurnTriage(
        intent="CHAT_THINK",
        profile="conversational",
        thinking=True,
        goals=[],
        hints=[],
    )
    history = [
        {"role": "user", "content": "Set up Telegram bot for Babo"},
        {"role": "assistant", "content": "Skill review created. telegram-channel."},
    ]
    reconcile_triage_continuation_phase(
        triage, "ok server restarted", history=history,
    )
    lowered = {h.lower() for h in triage.hints}
    assert "setup:configure_bundled" in lowered
    assert "setup:native_skill" not in lowered
    assert "telegram-channel" in " ".join(triage.goals).lower()
