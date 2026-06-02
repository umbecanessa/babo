"""Sheets tools -- read and write spreadsheet data.

Write operations support a confirmation gate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


def _not_connected() -> ToolResult:
    return ToolResult(
        content="Error: Google account not connected. Use google_workspace_connect first.",
        is_error=True,
    )


class SheetsInfoTool:
    """List sheet/tab names and metadata for a Google Spreadsheet."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "sheets_info"

    @property
    def description(self) -> str:
        return (
            "List all sheet (tab) names and metadata in a Google Spreadsheet. "
            "Use this to discover tab names before reading or writing data."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID (from the URL)",
                },
            },
            "required": ["spreadsheet_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        spreadsheet_id = params.get("spreadsheet_id", "")
        if not spreadsheet_id:
            return ToolResult(
                content="Error: spreadsheet_id is required", is_error=True,
            )

        try:
            service = await asyncio.to_thread(flow.build_service, "sheets", "v4")
            meta = await asyncio.to_thread(
                lambda: service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields="properties.title,sheets.properties",
                ).execute()
            )
            title = meta.get("properties", {}).get("title", "(untitled)")
            sheets = meta.get("sheets", [])

            lines: list[str] = []
            for sheet in sheets:
                props = sheet.get("properties", {})
                tab_name = props.get("title", "?")
                sheet_id = props.get("sheetId", "?")
                rows = props.get("gridProperties", {}).get("rowCount", 0)
                cols = props.get("gridProperties", {}).get("columnCount", 0)
                hidden = props.get("hidden", False)
                hidden_tag = " [HIDDEN]" if hidden else ""
                lines.append(
                    f"- **{tab_name}**{hidden_tag}\n"
                    f"  Sheet ID: {sheet_id} | Rows: {rows} | Cols: {cols}"
                )

            return ToolResult(
                content=(
                    f"**{title}** ({len(sheets)} sheet(s)):\n\n"
                    + "\n\n".join(lines)
                ),
                details={
                    "spreadsheet_title": title,
                    "sheet_count": len(sheets),
                    "sheet_names": [
                        s.get("properties", {}).get("title", "")
                        for s in sheets
                    ],
                },
            )
        except Exception as exc:
            return ToolResult(content=f"Sheets info failed: {exc}", is_error=True)


class SheetsReadTool:
    """Read cells or ranges from a Google Spreadsheet."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "sheets_read"

    @property
    def description(self) -> str:
        return (
            "Read data from a Google Spreadsheet. Provide the spreadsheet ID "
            "and a range in A1 notation (e.g. 'Sheet1!A1:D10'). "
            "Returns the data as a formatted table."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID (from the URL)",
                },
                "range": {
                    "type": "string",
                    "description": "Cell range in A1 notation (e.g. 'Sheet1!A1:D10')",
                },
            },
            "required": ["spreadsheet_id", "range"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        spreadsheet_id = params.get("spreadsheet_id", "")
        range_str = params.get("range", "")

        if not spreadsheet_id or not range_str:
            return ToolResult(
                content="Error: spreadsheet_id and range are required",
                is_error=True,
            )

        try:
            service = await asyncio.to_thread(flow.build_service, "sheets", "v4")
            result = await asyncio.to_thread(
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=range_str,
                ).execute()
            )
            values = result.get("values", [])
            if not values:
                return ToolResult(content=f"No data in range {range_str}")

            # Format as markdown table
            table = _format_table(values)
            return ToolResult(
                content=f"**{range_str}** ({len(values)} rows):\n\n{table}",
                details={
                    "rows": len(values),
                    "cols": max(len(row) for row in values) if values else 0,
                },
            )
        except Exception as exc:
            return ToolResult(content=f"Sheets read failed: {exc}", is_error=True)


class SheetsWriteTool:
    """Write values to cells in a Google Spreadsheet."""

    def __init__(self, adapter: Any, agent_id: str, require_confirmation: bool = True) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._require_confirmation = require_confirmation

    @property
    def name(self) -> str:
        return "sheets_write"

    @property
    def description(self) -> str:
        return (
            "Write values to a Google Spreadsheet range. Provide the "
            "spreadsheet ID, range, and a 2D array of values. "
            "If confirmation required, first call without confirmed=true to preview."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID",
                },
                "range": {
                    "type": "string",
                    "description": "Target range in A1 notation (e.g. 'Sheet1!A1:C3')",
                },
                "values": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "description": "2D array of values to write (rows x columns)",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to write after reviewing the draft",
                },
            },
            "required": ["spreadsheet_id", "range", "values"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        spreadsheet_id = params.get("spreadsheet_id", "")
        range_str = params.get("range", "")
        values = params.get("values", [])
        confirmed = params.get("confirmed", False)

        if not spreadsheet_id or not range_str or not values:
            return ToolResult(
                content="Error: spreadsheet_id, range, and values are required",
                is_error=True,
            )

        if self._require_confirmation and not confirmed:
            table = _format_table(values)
            return ToolResult(
                content=(
                    f"**Draft write to {range_str}:**\n\n{table}\n\n"
                    "Present this to the user. If approved, call "
                    "sheets_write with the same parameters and confirmed=true."
                ),
                details={"draft": True, "needs_confirmation": True},
            )

        try:
            service = await asyncio.to_thread(flow.build_service, "sheets", "v4")
            result = await asyncio.to_thread(
                lambda: service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=range_str,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                ).execute()
            )
            updated = result.get("updatedCells", 0)
            self._adapter.audit(
                self._agent_id, "sheets_write",
                spreadsheet_id=spreadsheet_id, range=range_str,
                cells_updated=updated,
            )
            return ToolResult(
                content=f"Written {updated} cell(s) to {range_str}",
                details={"updated_cells": updated},
            )
        except Exception as exc:
            return ToolResult(content=f"Sheets write failed: {exc}", is_error=True)


def _format_table(values: list[list[Any]]) -> str:
    """Format a 2D array as a markdown table."""
    if not values:
        return "(empty)"

    # Normalize row lengths
    max_cols = max(len(row) for row in values)
    rows = [row + [""] * (max_cols - len(row)) for row in values]

    # Column widths
    widths = [
        max(len(str(rows[r][c])) for r in range(len(rows)))
        for c in range(max_cols)
    ]
    widths = [max(w, 3) for w in widths]

    lines: list[str] = []
    # Header (first row)
    header = " | ".join(str(rows[0][c]).ljust(widths[c]) for c in range(max_cols))
    lines.append(f"| {header} |")
    lines.append("| " + " | ".join("-" * w for w in widths) + " |")

    # Data rows
    for row in rows[1:]:
        line = " | ".join(str(row[c]).ljust(widths[c]) for c in range(max_cols))
        lines.append(f"| {line} |")

    return "\n".join(lines)
