"""Duplicate channel send detection."""

import json

from nls.agentic.evaluator import detect_stall
from nls.agentic.types import LoopConfig, LoopState
from nls.runtime.channel_send_dedup import (
    channel_send_fingerprint,
    find_duplicate_channel_send,
    format_duplicate_channel_send_nudge,
)


def test_channel_send_fingerprint_discord():
    sig = 'discord_send:{"channel_id": "123", "text": "hello\\nworld"}'
    fp = channel_send_fingerprint(sig)
    assert fp is not None
    assert fp.startswith("discord_send|123|")
    assert len(fp.split("|")[-1]) == 16


def test_long_text_uses_first_and_last_two_hundred():
    head = "A" * 200
    tail = "Z" * 200
    middle = "M" * 500
    long_text = head + middle + tail
    sig = f'discord_send:{{"channel_id": "1", "text": {json.dumps(long_text)}}}'
    fp1 = channel_send_fingerprint(sig)
    fp2 = channel_send_fingerprint(sig.replace("Z", "Z"))  # same
    assert fp1 == fp2
    # Different tail → different fingerprint
    sig_other = f'discord_send:{{"channel_id": "1", "text": {json.dumps(head + middle + ("Y" * 200))}}}'
    assert channel_send_fingerprint(sig_other) != fp1


def test_channel_send_fingerprint_channel_remote():
    sig = (
        'channel_remote:{"channel": "discord", "action": "send", '
        '"channel_id": "999", "text": "hi"}'
    )
    fp = channel_send_fingerprint(sig)
    assert fp is not None
    assert fp.startswith("channel_remote|discord:999|")


def test_find_duplicate_channel_send():
    sig = 'discord_send:{"channel_id": "123", "text": "same"}'
    target, tool = find_duplicate_channel_send([sig, sig]) or ("", "")
    assert target == "123"
    assert tool == "discord_send"


def test_different_targets_not_duplicate():
    a = 'discord_send:{"channel_id": "1", "text": "same"}'
    b = 'discord_send:{"channel_id": "2", "text": "same"}'
    assert find_duplicate_channel_send([a, b]) is None


def test_duplicate_discord_send_triggers_stall_nudge():
    state = LoopState()
    cfg = LoopConfig(max_iterations=50, enable_delegation=True)
    sig = 'discord_send:{"channel_id": "1511320793634574387", "text": "Welcome"}'
    for _ in range(2):
        state.tool_call_signatures.append(sig)
        state.tool_history.append(("discord_send", False))
        state.tool_successes["discord_send"] = state.tool_successes.get("discord_send", 0) + 1

    msg = detect_stall(state, cfg)
    assert msg is not None
    assert "already sent" in msg.lower()
    assert "1511320793634574387" in msg


def test_nudge_mentions_verification_tools():
    text = format_duplicate_channel_send_nudge("123", "discord_send")
    assert "channel_remote" in text
    assert "channel_history" in text


def test_truncated_signature_still_fingerprints():
    sig = (
        'discord_send:{"channel_id": "1511320793634574387", "text": '
        '"Welcome to Babo with a long message that keeps going and going '
    )
    fp = channel_send_fingerprint(sig)
    assert fp is not None
    assert "1511320793634574387" in fp


def test_retry_after_failed_send_not_duplicate():
    sig = 'discord_send:{"channel_id": "123", "text": "hello"}'
    assert find_duplicate_channel_send(
        [sig, sig],
        tool_history=[("discord_send", True), ("discord_send", False)],
    ) is None


def test_exact_truncated_signature_duplicate():
    sig = (
        'discord_send:{"channel_id": "999", "text": "same long prefix that '
        "gets cut off identically"
    )
    dup = find_duplicate_channel_send([sig, sig], tool_history=[("discord_send", False)] * 2)
    assert dup is not None
    assert dup[0] == "999"
