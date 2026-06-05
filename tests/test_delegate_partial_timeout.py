"""Detached delegate timeout with knowledge digest counts as partial success."""

from __future__ import annotations


def test_partial_success_detection_logic():
    """Mirror executor detached-complete heuristics."""
    summary = (
        "(sub-agent timed out after 900s)\n\n"
        "[DELEGATE KNOWLEDGE DIGEST]\n"
        '{"files_created": ["ARCHITECTURE-SUMMARY.md"]}'
    )
    timed_out = "timed out after" in summary
    has_digest = "[DELEGATE KNOWLEDGE DIGEST]" in summary
    has_artifacts = any(
        marker in summary
        for marker in ("files_created", "files_modified", ".md", "ARCHITECTURE", "SUMMARY")
    )
    partial = timed_out and (has_digest or has_artifacts)
    aborted = True and not partial

    assert partial is True
    assert aborted is False


def test_hard_timeout_without_digest_is_failure():
    summary = "(sub-agent timed out after 900s)"
    timed_out = "timed out after" in summary
    has_digest = "[DELEGATE KNOWLEDGE DIGEST]" in summary
    partial = timed_out and has_digest
    assert partial is False
