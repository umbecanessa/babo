"""Offer-download tool -- Let the agent present a file for the user to download.

The agent calls this tool when it wants to give the user a file
(created via write, bash, etc.).  The frontend renders a download
card with the file name, size, and a download button.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import ToolResult, format_size


from .write import _resolve_path  # shared dedup-aware resolver


class OfferDownloadTool:
    """Offer a workspace file for the user to download.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    @property
    def name(self) -> str:
        return "offer_download"

    @property
    def description(self) -> str:
        return (
            "Offer a file from the workspace for the user to download. "
            "Use this after creating or modifying a file that the user "
            "should receive (e.g. generated reports, images, exports)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the file in the workspace (relative or absolute)"
                    ),
                },
                "label": {
                    "type": "string",
                    "description": (
                        "Optional display name for the download "
                        "(defaults to the filename)"
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "")
        label = params.get("label", "")

        if not path_str:
            return ToolResult(
                content="Error: 'path' is required.", is_error=True,
            )

        resolved = _resolve_path(path_str, self._cwd)

        if not resolved.is_file():
            return ToolResult(
                content=f"Error: File not found: {path_str}",
                is_error=True,
            )

        size = resolved.stat().st_size
        display_name = label or resolved.name

        return ToolResult(
            content=(
                f"Offered '{display_name}' ({format_size(size)}) "
                f"for download."
            ),
            details={
                "type": "offer_download",
                "path": path_str,
                "name": display_name,
                "size": size,
                "size_human": format_size(size),
            },
        )


def create_offer_download_tool(cwd: str) -> OfferDownloadTool:
    """Factory: create an offer_download tool for a working directory."""
    return OfferDownloadTool(cwd)
