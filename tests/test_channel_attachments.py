"""Tests for cross-channel attachment helpers and inbound normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncio
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module(skill_dir: str):
    path = ROOT / "nls" / "skills" / "bundled" / skill_dir / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"{skill_dir}_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


DiscordAdapter = _load_adapter_module("discord-channel").DiscordAdapter
SlackAdapter = _load_adapter_module("slack-channel").SlackAdapter


class _Ctx:
    _skills_dir = ROOT / "data" / "skills"

    def load_all_agent_configs(self) -> dict[str, dict]:
        return {}

    def save_config(self, cfg: dict, agent_id: str) -> None:
        pass


def _discord_adapter() -> DiscordAdapter:
    adapter = DiscordAdapter(global_config={}, ctx=_Ctx())
    adapter._agent_configs["agent-1"] = {"bot_token": "test-token"}
    adapter._bot_ids["agent-1"] = "999"
    return adapter


def _slack_adapter() -> SlackAdapter:
    adapter = SlackAdapter(global_config={}, ctx=_Ctx())
    adapter._agent_configs["agent-1"] = {"bot_token": "xoxb-test"}
    return adapter


def test_channel_history_content():
    from nls.skills.channel_adapter_util import channel_history_content

    assert channel_history_content("hello", []) == "hello"
    assert channel_history_content("", [{"path": "uploads/a.png"}]) == "[media]"
    assert channel_history_content("", []) == "[empty]"


def test_note_attachment_download_gaps():
    from nls.skills.channel_attachments import note_attachment_download_gaps

    assert note_attachment_download_gaps("hi", expected=0, saved=0) == "hi"
    out = note_attachment_download_gaps("hello", expected=2, saved=1, labels=["a.png"])
    assert "1 of 2" in out
    assert "a.png" in out
    assert "hello" in out


def test_detect_outbound_workspace_files(tmp_path: Path):
    from nls.skills.channel_attachments import detect_outbound_workspace_files

    agent_id = "agent-1"
    workspace = tmp_path / "agents" / agent_id / "workspace"
    uploads = workspace / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "report.pdf").write_bytes(b"%PDF")

    with patch("nls.skills.channel_adapter_util.resolve_workspace_file") as mock_resolve:
        mock_resolve.side_effect = lambda aid, path: uploads / "report.pdf" if path == "uploads/report.pdf" else None
        found = detect_outbound_workspace_files(
            "Here is uploads/report.pdf for you.", agent_id,
        )
    assert found == ["uploads/report.pdf"]


@pytest.mark.asyncio
async def test_try_feed_pending_answer_async_with_attachments(tmp_path: Path):
    from nls.skills.channel_processing import (
        _pending_queues,
        try_feed_pending_answer_async,
    )

    q: asyncio.Queue = asyncio.Queue()
    _pending_queues[("agent-1", "discord:dm:1")] = q

    app = MagicMock()
    app.state.agent_manager = MagicMock()
    app.state.agent_manager.agents_dir = tmp_path / "agents"

    fed = await try_feed_pending_answer_async(
        "agent-1",
        "discord:dm:1",
        "",
        attachments=[{
            "name": "pic.png",
            "path": "uploads/pic.png",
            "mime_type": "image/png",
            "size": 10,
        }],
        app=app,
    )
    assert fed is True
    answer = q.get_nowait()
    assert "pic.png" in answer
    _pending_queues.pop(("agent-1", "discord:dm:1"), None)


def test_discord_normalize_accepts_attachment_only_message():
    adapter = _discord_adapter()
    msg = {
        "author": {"id": "111", "username": "user"},
        "channel_id": "555",
        "guild_id": "777",
        "content": "",
        "attachments": [{"id": "1", "filename": "photo.png", "url": "https://cdn.example/a.png"}],
        "mentions": [{"id": "999"}],
    }
    normalized = adapter.normalize_gateway_message(msg, "agent-1")
    assert normalized is not None
    assert normalized["content"] == ""
    assert normalized["attachments"] == []


def test_discord_normalize_accepts_sticker_only_message():
    adapter = _discord_adapter()
    msg = {
        "author": {"id": "111", "username": "user"},
        "channel_id": "555",
        "guild_id": "777",
        "content": "",
        "attachments": [],
        "stickers": [{"id": "s1", "name": "wave"}],
        "mentions": [{"id": "999"}],
    }
    normalized = adapter.normalize_gateway_message(msg, "agent-1")
    assert normalized is not None
    assert normalized["content"] == "[sticker]"


def test_discord_normalize_skips_empty_message():
    adapter = _discord_adapter()
    msg = {
        "author": {"id": "111", "username": "user"},
        "channel_id": "555",
        "guild_id": "777",
        "content": "",
        "attachments": [],
        "mentions": [],
    }
    assert adapter.normalize_gateway_message(msg, "agent-1") is None


def test_slack_normalize_accepts_file_only_message():
    adapter = _slack_adapter()
    event = {
        "type": "message",
        "channel": "C123",
        "user": "U456",
        "text": "",
        "files": [{"id": "F1", "name": "report.pdf", "url_private": "https://files.slack.com/a"}],
    }
    normalized = adapter.normalize_event(event, "agent-1")
    assert normalized is not None
    assert normalized["content"] == ""


def test_slack_normalize_skips_empty_message():
    adapter = _slack_adapter()
    event = {
        "type": "message",
        "channel": "C123",
        "user": "U456",
        "text": "",
        "files": [],
    }
    assert adapter.normalize_event(event, "agent-1") is None


def test_save_bytes_to_uploads(tmp_path: Path):
    from nls.skills import channel_attachments as ca

    mock_am = MagicMock()
    mock_am.agents_dir = tmp_path / "agents"
    uploads = mock_am.agents_dir / "agent-1" / "workspace" / "uploads"
    uploads.mkdir(parents=True)

    with patch.object(ca, "agent_uploads_dir", return_value=uploads):
        record = ca.save_bytes_to_uploads(
            "agent-1",
            filename="hello.txt",
            data=b"hello",
            mime_type="text/plain",
        )
    assert record is not None
    assert record["path"] == "uploads/hello.txt"
    assert (uploads / "hello.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_discord_download_inbound_attachments(tmp_path: Path):
    from nls.skills import channel_attachments as ca

    adapter = _discord_adapter()
    uploads = tmp_path / "agents" / "agent-1" / "workspace" / "uploads"
    uploads.mkdir(parents=True)

    async def _fake_download(agent_id, url, **kwargs):
        return ca.save_bytes_to_uploads(
            agent_id,
            filename=kwargs["filename"],
            data=b"png-bytes",
            mime_type=kwargs.get("mime_type", "image/png"),
        )

    with patch.object(ca, "download_url_to_uploads", side_effect=_fake_download):
        with patch.object(ca, "agent_uploads_dir", return_value=uploads):
            saved = await adapter.download_inbound_attachments(
                {
                    "attachments": [
                        {
                            "id": "1",
                            "filename": "pic.png",
                            "url": "https://cdn.discord.com/pic.png",
                            "content_type": "image/png",
                        }
                    ]
                },
                "agent-1",
            )
    assert len(saved) == 1
    assert saved[0]["name"] == "pic.png"
    assert saved[0]["path"] == "uploads/pic.png"


@pytest.mark.asyncio
async def test_slack_resolve_slack_file_uses_files_info():
    adapter = _slack_adapter()

    async def _fake_api(token, method, payload):
        assert method == "files.info"
        return {"ok": True, "file": {"url_private_download": "https://files.slack.com/dl/1"}}

    with patch.object(adapter, "_api_post", side_effect=_fake_api):
        url, meta = await adapter._resolve_slack_file("xoxb-test", {"id": "F123"})
    assert url == "https://files.slack.com/dl/1"
    assert meta["id"] == "F123"


@pytest.mark.asyncio
async def test_email_resend_attachment_download(tmp_path: Path):
    import importlib

    spec = importlib.util.spec_from_file_location(
        "email_webhook",
        ROOT / "nls" / "skills" / "bundled" / "email-channel" / "webhook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    from nls.skills import channel_attachments as ca

    async def _fake_meta(email_id, attachment_id, api_key):
        return {
            "download_url": "https://cdn.example/file.pdf",
            "filename": "file.pdf",
            "content_type": "application/pdf",
        }

    async def _fake_download(agent_id, url, **kwargs):
        return ca.save_bytes_to_uploads(
            agent_id, filename="file.pdf", data=b"pdf", mime_type="application/pdf",
        )

    uploads = tmp_path / "agents" / "agent-1" / "workspace" / "uploads"
    uploads.mkdir(parents=True)

    with patch.object(mod, "_resend_api_key", return_value="re_test"):
        with patch.object(mod, "_fetch_resend_attachment_url", side_effect=_fake_meta):
            with patch.object(ca, "download_url_to_uploads", side_effect=_fake_download):
                with patch.object(ca, "agent_uploads_dir", return_value=uploads):
                    saved = await mod._save_email_attachments(
                        [{"id": "att-1", "filename": "file.pdf"}],
                        "agent-1",
                        MagicMock(),
                        email_id="email-1",
                    )
    assert len(saved) == 1
    assert saved[0]["path"] == "uploads/file.pdf"


@pytest.mark.asyncio
async def test_email_attachment_only_not_no_content():
    import importlib

    spec = importlib.util.spec_from_file_location(
        "email_webhook",
        ROOT / "nls" / "skills" / "bundled" / "email-channel" / "webhook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    app = MagicMock()
    app.state.agent_manager = None

    body = {
        "data": {
            "from": "user@example.com",
            "subject": "Files",
            "text": "",
        },
        "_full_email": {
            "from": "user@example.com",
            "subject": "Files",
            "text": "",
            "attachments": [{"filename": "a.pdf", "content": "YWJj"}],
        },
    }
    result = await mod.process_inbound_email(app, "agent-1", body)
    assert result.get("status") != "no_content"
