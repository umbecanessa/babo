"""Tests for orchestration dispatch source classification."""

from nls.runtime.dispatch_sources import is_orchestration_dispatch_source


def test_scheduler_and_delegate_batch_are_orchestration():
    assert is_orchestration_dispatch_source("scheduler")
    assert is_orchestration_dispatch_source("delegate_batch_complete")
    assert is_orchestration_dispatch_source("team_checkback:team_abc")


def test_user_and_channel_are_not_orchestration():
    assert not is_orchestration_dispatch_source("user")
    assert not is_orchestration_dispatch_source("whatsapp")


def test_dmn_and_drive_are_orchestration():
    assert is_orchestration_dispatch_source("dmn")
    assert is_orchestration_dispatch_source("drive:curiosity")
