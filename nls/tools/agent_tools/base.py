"""AgentTool protocol and ToolResult -- the foundation of the new tool system.

Inspired by pi-agent-core's AgentTool interface, this module defines
the minimal contract that all NLS tools must satisfy.  Four core tools
(read, write, edit, bash) implement this protocol; extensions like the
browser tool also conform to it.

Design principles:
    - Tools are async-first (I/O-bound operations dominate)
    - Each tool is a plain object with ``execute()`` -- no registry, no
      biological metadata, no JSON templates
    - Tools return structured ``ToolResult`` with content + details
    - Abort via ``asyncio.Event`` for cooperative cancellation
    - Factory functions (``create_xxx_tool(cwd)``) allow per-agent config
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Tool result
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Structured result from a tool execution.

    Attributes
    ----------
    content : str
        The text content returned to the model.
    is_error : bool
        Whether the execution failed (model sees this as an error).
    details : dict
        UI/logging metadata (diff, truncation info, exit codes, etc.)
        Not sent to the model -- used by the frontend and event stream.
    """

    content: str
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    stop_loop: bool = False
    blocked_by_hook: bool = False


# ---------------------------------------------------------------------------
# Tool protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentTool(Protocol):
    """Minimal interface for an NLS agent tool.

    Every tool must expose:
        - ``name``        -- unique identifier (e.g., "read", "bash")
        - ``description`` -- one-line description for the model
        - ``parameters``  -- JSON Schema dict for the tool's parameters
        - ``execute()``   -- async execution returning a ToolResult
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        """Execute the tool with the given parameters.

        Parameters
        ----------
        params : dict
            Validated parameters matching ``self.parameters`` schema.
        signal : asyncio.Event | None
            If set, the tool should abort as soon as practical.

        Returns
        -------
        ToolResult
            The execution result (content for the model, plus metadata).
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tool_to_openai_schema(tool: AgentTool) -> dict[str, Any]:
    """Convert a single AgentTool to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def tools_to_openai_schema(tools: list[AgentTool]) -> list[dict[str, Any]]:
    """Convert a list of AgentTools to OpenAI function-calling format.

    This is the schema list passed to vLLM's ``tools`` parameter.
    Malformed tools are silently skipped to prevent a single broken
    skill from crashing the entire agent.
    """
    schemas = []
    for t in tools:
        try:
            schemas.append(tool_to_openai_schema(t))
        except Exception:
            pass
    return schemas


def tools_to_directory(tools: list[AgentTool]) -> str:
    """Build a compact tool directory for the system prompt.

    Keeps the first two sentences (up to 250 chars) so that critical
    context like "use contacts tool first" or owner identities survive.
    """
    lines = ["Your available tools:"]
    for t in tools:
        try:
            desc = t.description.split("\n")[0].strip()
            if len(desc) > 250:
                # Keep first two sentences if possible
                second_dot = desc.find(". ", desc.find(". ") + 1)
                if second_dot > 0 and second_dot < 250:
                    desc = desc[: second_dot + 1]
                else:
                    first_dot = desc.find(". ")
                    if first_dot > 0:
                        desc = desc[: first_dot + 1]
            lines.append(f"- {t.name}: {desc}")
        except Exception:
            pass
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Truncation utilities (shared by read and bash tools)
# ---------------------------------------------------------------------------

DEFAULT_MAX_LINES = 500
DEFAULT_MAX_BYTES = 30_000  # ~30 KB


def format_size(size_bytes: int) -> str:
    """Human-readable byte size."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def truncate_tail(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str, bool, dict[str, Any]]:
    """Keep the *last* N lines / max bytes (for bash output).

    Returns (truncated_text, was_truncated, details).
    """
    lines = text.split("\n")
    total_lines = len(lines)

    # Line limit first
    if total_lines > max_lines:
        lines = lines[-max_lines:]
        truncated_by = "lines"
    else:
        truncated_by = None

    joined = "\n".join(lines)

    # Byte limit
    encoded = joined.encode("utf-8")
    if len(encoded) > max_bytes:
        encoded = encoded[-max_bytes:]
        joined = encoded.decode("utf-8", errors="ignore")
        # Re-split to drop any partial first line
        remaining = joined.split("\n", 1)
        if len(remaining) > 1:
            joined = remaining[1]
        truncated_by = "bytes"

    was_truncated = truncated_by is not None
    details: dict[str, Any] = {}
    if was_truncated:
        output_lines = joined.count("\n") + 1
        details = {
            "truncated": True,
            "truncated_by": truncated_by,
            "total_lines": total_lines,
            "output_lines": output_lines,
            "output_bytes": len(joined.encode("utf-8")),
        }

    return joined, was_truncated, details


def truncate_head(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str, bool, dict[str, Any]]:
    """Keep the *first* N lines / max bytes (for read output).

    Returns (truncated_text, was_truncated, details).
    """
    lines = text.split("\n")
    total_lines = len(lines)

    if total_lines > max_lines:
        lines = lines[:max_lines]
        truncated_by = "lines"
    else:
        truncated_by = None

    joined = "\n".join(lines)

    encoded = joined.encode("utf-8")
    if len(encoded) > max_bytes:
        encoded = encoded[:max_bytes]
        joined = encoded.decode("utf-8", errors="ignore")
        # Drop partial last line
        if "\n" in joined:
            joined = joined.rsplit("\n", 1)[0]
        truncated_by = "bytes"

    was_truncated = truncated_by is not None
    details: dict[str, Any] = {}
    if was_truncated:
        output_lines = joined.count("\n") + 1
        details = {
            "truncated": True,
            "truncated_by": truncated_by,
            "total_lines": total_lines,
            "output_lines": output_lines,
            "output_bytes": len(joined.encode("utf-8")),
        }

    return joined, was_truncated, details
