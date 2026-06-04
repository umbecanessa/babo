"""Squad tool — persistent multi-agent coordination."""

from __future__ import annotations

import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


class SquadTool:
    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad"

    @property
    def description(self) -> str:
        return (
            "Coordinate your persistent squad — shared inbox, lead approval, "
            "and member assignments.\n"
            "Members: propose, inspect, list_inbox.\n"
            "Lead: approve, reject, assign, reassign, resolve_escalation, brief, "
            "checkback, pause, resume, status.\n"
            "Workflow: squad(action='propose', title='...', assignee_agent_id='...') "
            "→ lead squad(action='approve', item_id='...')."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "inspect", "list_inbox", "propose", "approve", "reject",
                        "assign", "reassign", "resolve_escalation", "brief",
                        "checkback", "pause", "resume", "status",
                    ],
                },
                "squad_id": {"type": "string", "description": "Squad ID (optional if you belong to one)."},
                "item_id": {"type": "string", "description": "Inbox item ID for approve/reject."},
                "assignee_agent_id": {"type": "string", "description": "Target member agent ID."},
                "target_agent_id": {"type": "string", "description": "Member to brief."},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "idle_eligible": {"type": "boolean", "description": "Pick up in idle when approved."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "reject_reason": {"type": "string"},
                "message": {"type": "string", "description": "Brief or filter (list_inbox status)."},
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = self._sm.handle_action(
                self._caller,
                kwargs.get("action", ""),
                squad_id=kwargs.get("squad_id", ""),
                item_id=kwargs.get("item_id", ""),
                assignee_agent_id=kwargs.get("assignee_agent_id", ""),
                target_agent_id=kwargs.get("target_agent_id", ""),
                title=kwargs.get("title", ""),
                description=kwargs.get("description", ""),
                priority=kwargs.get("priority", "normal"),
                idle_eligible=bool(kwargs.get("idle_eligible", True)),
                tags=kwargs.get("tags"),
                reason=kwargs.get("message", ""),
                reject_reason=kwargs.get("reject_reason", ""),
                message=kwargs.get("message", ""),
            )
            import json
            return ToolResult(content=json.dumps(result, indent=2))
        except PermissionError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            logger.warning("squad tool failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)


class SquadEscalateTool:
    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_escalate"

    @property
    def description(self) -> str:
        return (
            "Escalate to your squad lead when stuck, blocked by trust policy, "
            "or needing a policy decision. Does not contact the owner directly."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {"type": "string"},
                "context": {"type": "string", "description": "Summary for the lead."},
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = self._sm.escalate(
                self._caller,
                reason=kwargs.get("reason", ""),
                context=kwargs.get("context", ""),
            )
            import json
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


class SquadMessageTool:
    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_message"

    @property
    def description(self) -> str:
        return "Send an internal message to squad peer(s). High/urgent priority may wake idle members."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "to_agent_id": {"type": "string", "description": "Empty = broadcast to squad."},
                "priority": {"type": "string", "enum": ["normal", "high", "urgent"]},
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = self._sm.squad_message(
                self._caller,
                message=kwargs.get("message", ""),
                to_agent_id=kwargs.get("to_agent_id", ""),
                priority=kwargs.get("priority", "normal"),
            )
            import json
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


class SquadReportDoneTool:
    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_report_done"

    @property
    def description(self) -> str:
        return "Notify squad lead that an approved squad todo is complete."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["todo_id"],
            "properties": {
                "todo_id": {"type": "string"},
                "squad_id": {"type": "string"},
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = self._sm.report_done(
                self._caller,
                todo_id=kwargs.get("todo_id", ""),
                squad_id=kwargs.get("squad_id", ""),
            )
            import json
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)
