"""Channel outbound sanitization — block pseudo tool calls in public replies."""

from nls.runtime.response_cleanup import (
    is_channel_outbound_tool_leak,
    sanitize_channel_outbound,
)


def test_sanitize_strips_python_style_tool_call():
    raw = "channel_inspect(channel='discord')"
    assert sanitize_channel_outbound(raw) == ""
    assert is_channel_outbound_tool_leak(raw)


def test_sanitize_keeps_natural_language():
    text = "Here is my proposal for the channel layout…"
    assert sanitize_channel_outbound(text) == text


def test_sanitize_strips_xml_tool_call():
    raw = '<tool_call>{"name": "read", "arguments": {"path": "x"}}</tool_call>'
    assert sanitize_channel_outbound(raw) == ""


def test_sanitize_keeps_mixed_content():
    raw = "Looking at channels now.\nchannel_inspect(channel='discord')"
    cleaned = sanitize_channel_outbound(raw)
    assert "Looking at channels now." in cleaned
    assert "channel_inspect" not in cleaned
