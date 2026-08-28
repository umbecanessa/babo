"""Canonical workspace paths for agent PTY session keys."""

from __future__ import annotations

import os
from pathlib import Path


def default_agent_workspace(agent_id: str, *, agents_dir: Path | None = None) -> Path:
    aid = (agent_id or "").strip()
    if not aid:
        return Path.cwd()
    if agents_dir is not None:
        return (agents_dir / aid / "workspace").resolve()
    data_dir = os.environ.get("NLS_DATA_DIR", "").strip()
    if data_dir:
        return (Path(data_dir) / "agents" / aid / "workspace").resolve()
    return (Path("data") / "agents" / aid / "workspace").resolve()


def normalize_pty_workspace(
    agent_id: str,
    workspace: str,
    *,
    agents_dir: Path | None = None,
) -> str:
    """Return one stable absolute path for PTY pool keys (Windows-safe)."""
    raw = (workspace or "").strip()
    if not raw:
        return str(default_agent_workspace(agent_id, agents_dir=agents_dir))

    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = default_agent_workspace(agent_id, agents_dir=agents_dir)
        path = (base / path).resolve()
    else:
        try:
            path = path.resolve()
        except OSError:
            path = Path(os.path.abspath(str(path)))

    return str(path)
