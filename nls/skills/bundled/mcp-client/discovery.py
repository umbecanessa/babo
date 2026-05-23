"""Auto-discovery -- scan for MCP server configs in installed skills.

Two discovery sources:

1. **Installed skills** -- walk all skill directories under
   ``data/skills/`` looking for ``mcp_servers.json``.
2. **Saved servers** -- previously connected servers stored in the
   MCP client skill's own ``config.json``.

Both use the de-facto standard ``mcpServers`` config format (same as
Claude Desktop, Windsurf, Cline, etc.).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    """Parsed MCP server configuration."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    description: str = ""
    source: str = "discovered"  # "discovered" | "saved"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "source": self.source}
        if self.url:
            d["url"] = self.url
        if self.command:
            d["command"] = self.command
        if self.args:
            d["args"] = self.args
        if self.env:
            d["env"] = self.env
        if self.headers:
            d["headers"] = self.headers
        if self.description:
            d["description"] = self.description
        return d


def scan_mcp_configs(skills_dir: Path) -> dict[str, ServerConfig]:
    """Walk installed skill directories for ``mcp_servers.json`` files.

    Returns a dict mapping server name -> config.
    """
    configs: dict[str, ServerConfig] = {}
    if not skills_dir.is_dir():
        return configs

    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        mcp_file = skill_dir / "mcp_servers.json"
        if not mcp_file.exists():
            continue
        try:
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            for name, cfg in servers.items():
                configs[name] = _parse_server_entry(name, cfg, "discovered")
        except Exception as exc:
            logger.warning(
                "Failed to parse %s: %s", mcp_file, exc,
            )

    return configs


def load_saved_servers(config_path: Path) -> dict[str, ServerConfig]:
    """Load previously connected servers from the skill's config.json."""
    if not config_path.exists():
        return {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        saved = data.get("saved_servers", {})
        configs: dict[str, ServerConfig] = {}
        for name, cfg in saved.items():
            configs[name] = _parse_server_entry(name, cfg, "saved")
        return configs
    except Exception as exc:
        logger.warning("Failed to load saved servers: %s", exc)
        return {}


def save_server_config(config_path: Path, name: str, cfg: dict[str, Any]) -> None:
    """Persist a server config to config.json for auto-reconnect."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    saved = data.setdefault("saved_servers", {})
    saved[name] = cfg
    config_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def remove_saved_server(config_path: Path, name: str) -> None:
    """Remove a server from saved configs."""
    if not config_path.exists():
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        saved = data.get("saved_servers", {})
        saved.pop(name, None)
        config_path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_server_entry(
    name: str, cfg: dict[str, Any], source: str,
) -> ServerConfig:
    """Parse one entry from the ``mcpServers`` config."""
    return ServerConfig(
        name=name,
        command=cfg.get("command"),
        args=cfg.get("args", []),
        env=cfg.get("env", {}),
        url=cfg.get("url"),
        headers=cfg.get("headers", {}),
        description=cfg.get("description", ""),
        source=source,
    )
