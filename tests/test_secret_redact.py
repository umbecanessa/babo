"""Tests for secret redaction in logs."""

from nls.security.secret_redact import redact_secrets


def test_redact_discord_bot_token():
    # Synthetic token shape only — not a real credential.
    token = "MFAKEFAKEFAKEFAKEFAKEFAKEFA.FAKE00.fakeDiscordTokenForTestsOnly12"
    out, n = redact_secrets(f"token={token}")
    assert n >= 1
    assert token not in out
    assert "discord-bot-token" in out


def test_redact_github_pat():
    out, n = redact_secrets("ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert n >= 1
    assert "ghp_***" in out
