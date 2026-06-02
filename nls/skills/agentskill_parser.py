"""AgentSkills SKILL.md parser.

Parses the AgentSkills format: a directory containing a ``SKILL.md`` file
with YAML frontmatter and a Markdown instruction body.  Compatible with
the AgentSkills open spec (https://agentskills.io/) and the OpenClaw
``metadata.openclaw`` gating extensions.

Public API
----------
``parse_skill_md(path)``
    Parse a SKILL.md file and return an ``AgentSkillInfo`` dataclass.

``check_gating(info)``
    Evaluate the gating requirements (bins, env, OS) and return a
    ``GatingResult`` with ``eligible`` flag and failure reasons.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9]*(-[a-z0-9]+)*)?$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024
_MAX_COMPATIBILITY_LEN = 500


@dataclass
class AgentSkillInfo:
    """Parsed representation of a SKILL.md file."""

    name: str = ""
    description: str = ""
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    instructions: str = ""
    base_dir: str = ""

    # Extracted from metadata.openclaw / metadata.clawdbot for convenience
    requires_bins: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    requires_any_bins: list[str] = field(default_factory=list)
    os_filter: list[str] = field(default_factory=list)
    homepage: str | None = None
    primary_env: str | None = None
    always: bool = False
    install_instructions: list[dict[str, str]] = field(default_factory=list)

    # Populated from metadata if present
    author: str | None = None
    version: str | None = None


@dataclass
class GatingResult:
    """Result of evaluating gating requirements."""

    eligible: bool = True
    reasons: list[str] = field(default_factory=list)


class ParseError(Exception):
    """Raised when SKILL.md cannot be parsed."""


def parse_skill_md(path: Path) -> AgentSkillInfo:
    """Parse a SKILL.md file into an ``AgentSkillInfo``.

    Parameters
    ----------
    path:
        Path to the SKILL.md file (not the parent directory).

    Returns
    -------
    AgentSkillInfo with all fields populated.

    Raises
    ------
    ParseError
        If the file is missing, unreadable, or has invalid frontmatter.
    """
    if not path.exists():
        raise ParseError(f"SKILL.md not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ParseError(f"Cannot read {path}: {exc}") from exc

    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ParseError(f"No valid YAML frontmatter in {path}")

    raw_yaml = match.group(1)
    body = text[match.end():]

    try:
        fm = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ParseError(f"Invalid YAML frontmatter in {path}: {exc}") from exc

    if not isinstance(fm, dict):
        raise ParseError(f"Frontmatter must be a mapping, got {type(fm).__name__}")

    info = AgentSkillInfo()
    info.base_dir = str(path.parent)

    info.name = str(fm.get("name", "")).strip()
    info.description = str(fm.get("description", "")).strip()
    info.license = fm.get("license")
    info.compatibility = fm.get("compatibility")
    if isinstance(fm.get("homepage"), str):
        info.homepage = fm["homepage"]

    allowed_tools_raw = fm.get("allowed-tools", "")
    if isinstance(allowed_tools_raw, str) and allowed_tools_raw.strip():
        info.allowed_tools = allowed_tools_raw.strip().split()
    elif isinstance(allowed_tools_raw, list):
        info.allowed_tools = [str(t) for t in allowed_tools_raw]

    raw_metadata = fm.get("metadata")
    if isinstance(raw_metadata, dict):
        info.metadata = raw_metadata
    elif isinstance(raw_metadata, str):
        try:
            import json
            info.metadata = json.loads(raw_metadata)
        except Exception as exc:
            logger.debug("Metadata string is not valid JSON in %s: %s", path, exc)
            info.metadata = {}

    _extract_openclaw_fields(info)

    if isinstance(info.metadata.get("author"), str):
        info.author = info.metadata["author"]
    if isinstance(info.metadata.get("version"), str):
        info.version = info.metadata["version"]

    info.instructions = body.strip()
    if info.base_dir:
        info.instructions = info.instructions.replace("{baseDir}", info.base_dir)

    return info


def _extract_openclaw_fields(info: AgentSkillInfo) -> None:
    """Pull gating/install fields from metadata.

    ClawHub skills use ``metadata.clawdbot`` (preferred), but
    ``metadata.clawdis`` and ``metadata.openclaw`` are accepted aliases.
    """
    oc: dict[str, Any] = {}
    for key in ("clawdbot", "clawdis", "openclaw"):
        candidate = info.metadata.get(key, {})
        if isinstance(candidate, dict) and candidate:
            oc = candidate
            break

    if not oc:
        return

    requires = oc.get("requires", {})
    if isinstance(requires, dict):
        bins = requires.get("bins", [])
        info.requires_bins = [str(b) for b in bins] if isinstance(bins, list) else []

        any_bins = requires.get("anyBins", [])
        info.requires_any_bins = [str(b) for b in any_bins] if isinstance(any_bins, list) else []

        env = requires.get("env", [])
        info.requires_env = [str(e) for e in env] if isinstance(env, list) else []

    os_filter = oc.get("os", [])
    if isinstance(os_filter, list):
        info.os_filter = [str(o) for o in os_filter]

    if isinstance(oc.get("homepage"), str):
        info.homepage = oc["homepage"]
    if isinstance(oc.get("primaryEnv"), str):
        info.primary_env = oc["primaryEnv"]
    if oc.get("always") is True:
        info.always = True

    install_raw = oc.get("install", [])
    if isinstance(install_raw, list):
        for entry in install_raw:
            if isinstance(entry, dict):
                info.install_instructions.append({
                    k: str(v) for k, v in entry.items()
                })


def validate_skill_info(info: AgentSkillInfo) -> list[str]:
    """Validate parsed skill info against the AgentSkills spec.

    Returns a list of validation error strings (empty = valid).
    """
    errors: list[str] = []

    if not info.name:
        errors.append("'name' is required")
    elif len(info.name) > _MAX_NAME_LEN:
        errors.append(f"'name' exceeds {_MAX_NAME_LEN} chars")
    elif not _NAME_RE.match(info.name):
        errors.append(
            "'name' must be lowercase alphanumeric + hyphens, "
            "no leading/trailing/consecutive hyphens"
        )

    if not info.description:
        errors.append("'description' is required")
    elif len(info.description) > _MAX_DESCRIPTION_LEN:
        errors.append(f"'description' exceeds {_MAX_DESCRIPTION_LEN} chars")

    if info.compatibility and len(info.compatibility) > _MAX_COMPATIBILITY_LEN:
        errors.append(f"'compatibility' exceeds {_MAX_COMPATIBILITY_LEN} chars")

    return errors


def check_gating(info: AgentSkillInfo) -> GatingResult:
    """Evaluate whether this skill is eligible to load on the current host.

    Checks binary presence, environment variables, and OS filter.
    """
    if info.always:
        return GatingResult(eligible=True)

    result = GatingResult()

    if info.os_filter:
        current_platform = sys.platform
        if current_platform not in info.os_filter:
            result.eligible = False
            result.reasons.append(
                f"OS '{current_platform}' not in allowed list {info.os_filter}"
            )

    for bin_name in info.requires_bins:
        if not shutil.which(bin_name):
            result.eligible = False
            result.reasons.append(f"Required binary '{bin_name}' not found on PATH")

    if info.requires_any_bins:
        if not any(shutil.which(b) for b in info.requires_any_bins):
            result.eligible = False
            result.reasons.append(
                f"None of required binaries {info.requires_any_bins} found on PATH"
            )

    for env_var in info.requires_env:
        if env_var not in os.environ:
            result.eligible = False
            result.reasons.append(f"Required env var '{env_var}' not set")

    return result
