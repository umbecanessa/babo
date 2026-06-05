"""Tests for Discord credential WM slot policy."""

from __future__ import annotations

from nls.runtime.channel_credential_policy import (
    is_discord_bot_token,
    prepare_wm_credential_slot,
    upsert_wm_credential,
)


def test_is_discord_bot_token_shape():
    assert is_discord_bot_token(
        "MTk4NjIyNDY0NDU2OTQ1Mzg4.ClFz7X.ZRmBn7aWDm6OvUfe8x1Q7j4",
    )
    assert not is_discord_bot_token("not-a-token")


def test_prepare_wm_redacts_discord_domain_token():
    out = prepare_wm_credential_slot(
        "Project.Credential.Discord.Mod",
        "MTk4NjIyNDY0NDU2OTQ1Mzg4.ClFz7X.ZRmBn7aWDm6OvUfe8x1Q7j4",
    )
    assert out is not None
    assert "skill config" in out.lower()
    assert "ClFz7X" not in out


def test_prepare_wm_skips_raw_token_outside_discord_domain():
    assert prepare_wm_credential_slot("Project.API.Key", "MTk4NjIyNDY0NDU2OTQ1Mzg4.ClFz7X.ZRmBn7aWDm6OvUfe8x1Q7j4") is None


class _FakeWM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def upsert_credential(self, *, domain: str, content: str, source: str, salience: float) -> None:
        self.calls.append((domain, content))


def test_upsert_wm_credential_redacts():
    wm = _FakeWM()
    ok = upsert_wm_credential(
        wm,
        domain="Project.Credential.Discord.QA",
        fact="MTk4NjIyNDY0NDU2OTQ1Mzg4.ClFz7X.ZRmBn7aWDm6OvUfe8x1Q7j4",
    )
    assert ok
    assert len(wm.calls) == 1
    assert "ClFz7X" not in wm.calls[0][1]
