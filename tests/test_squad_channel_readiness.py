"""Multi-platform squad channel readiness routing."""

from __future__ import annotations

from nls.runtime.squad_channel_readiness import resolve_channel_platform


def test_resolve_platform_explicit():
    assert resolve_channel_platform("123", channel="telegram") == "telegram"
    assert resolve_channel_platform("C01234567", channel="discord") == "discord"


def test_resolve_platform_from_id_shape():
    assert resolve_channel_platform("1511069841887330434") == "discord"
    assert resolve_channel_platform("C0123456789") == "slack"
    assert resolve_channel_platform("-1001234567890") == "telegram"
