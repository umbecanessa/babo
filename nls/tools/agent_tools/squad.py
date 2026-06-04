"""Squad tools — bootstrap, coordination, job/trust governance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)

_MEMBER_ACTIONS = frozenset({
    "inspect",
    "list_inbox",
    "propose",
})

_LEAD_ONLY_ACTIONS = frozenset({
    "approve",
    "reject",
    "assign",
    "reassign",
    "resolve_escalation",
    "brief",
    "checkback",
    "pause",
    "resume",
    "status",
    "remove_member",
    "disband_member",
    "add_member",
    "request_delete_member",
    "list_pending",
    "pause_member",
    "resume_member",
    "spawn_member",
    "set_member_job",
    "set_lead_job",
    "request_trust_change",
})

_ASYNC_ACTIONS = frozenset({
    "pause_member",
    "resume_member",
    "spawn_member",
})

_ALL_ACTIONS = _MEMBER_ACTIONS | _LEAD_ONLY_ACTIONS

_SHARED_PARAMS: dict[str, Any] = {
    "squad_id": {"type": "string", "description": "Squad ID (optional if you belong to one)."},
    "item_id": {"type": "string", "description": "Inbox item ID for approve/reject."},
    "assignee_agent_id": {"type": "string", "description": "Target member agent ID."},
    "target_agent_id": {"type": "string", "description": "Member to brief, remove, pause, job/trust, or delete."},
    "title": {"type": "string", "description": "Task title, job title, or spawn member name."},
    "description": {"type": "string", "description": "Mission, job brief, or spawn context."},
    "job_persona": {"type": "string", "description": "Job persona / voice for set_*_job."},
    "job_playbook": {"type": "string", "description": "Job playbook for set_*_job."},
    "default_profile": {
        "type": "string",
        "description": "Orchestration profile for job (e.g. squad_lead, solo_structured).",
    },
    "owner_confirmed": {
        "type": "boolean",
        "description": "True after ask_user() owner approval (create squad, set_lead_job).",
    },
    "genesis_version": {
        "type": "string",
        "description": "Genesis template for spawn_member (defaults to server default).",
    },
    "tools_allow": {"type": "array", "items": {"type": "string"}},
    "tools_deny": {"type": "array", "items": {"type": "string"}},
    "action_classes_allow": {"type": "array", "items": {"type": "string"}},
    "action_classes_deny": {"type": "array", "items": {"type": "string"}},
    "in_scope": {"type": "array", "items": {"type": "string"}},
    "out_of_scope": {"type": "array", "items": {"type": "string"}},
    "channel_overlays": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "channel_key": {"type": "string"},
                "profile_cap": {"type": "string"},
                "tools_allow": {"type": "array", "items": {"type": "string"}},
                "tools_deny": {"type": "array", "items": {"type": "string"}},
                "public_channel": {"type": "boolean"},
            },
        },
        "description": "Per-channel trust caps for request_trust_change.",
    },
    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
    "idle_eligible": {"type": "boolean", "description": "Pick up in idle when approved."},
    "tags": {"type": "array", "items": {"type": "string"}},
    "reject_reason": {"type": "string"},
    "message": {"type": "string", "description": "Brief, delete reason, or list_inbox status filter."},
}


def _pass_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "squad_id": kwargs.get("squad_id", ""),
        "item_id": kwargs.get("item_id", ""),
        "assignee_agent_id": kwargs.get("assignee_agent_id", ""),
        "target_agent_id": kwargs.get("target_agent_id", ""),
        "title": kwargs.get("title", ""),
        "description": kwargs.get("description", ""),
        "priority": kwargs.get("priority", "normal"),
        "idle_eligible": bool(kwargs.get("idle_eligible", True)),
        "tags": kwargs.get("tags"),
        "reason": kwargs.get("message", ""),
        "reject_reason": kwargs.get("reject_reason", ""),
        "message": kwargs.get("message", ""),
        "owner_confirmed": bool(kwargs.get("owner_confirmed", False)),
        "job_persona": kwargs.get("job_persona", ""),
        "job_playbook": kwargs.get("job_playbook", ""),
        "default_profile": kwargs.get("default_profile", ""),
        "in_scope": kwargs.get("in_scope"),
        "out_of_scope": kwargs.get("out_of_scope"),
        "tools_allow": kwargs.get("tools_allow"),
        "tools_deny": kwargs.get("tools_deny"),
        "action_classes_allow": kwargs.get("action_classes_allow"),
        "action_classes_deny": kwargs.get("action_classes_deny"),
        "channel_overlays": kwargs.get("channel_overlays"),
    }


class SquadSetupTool:
    """Bootstrap a persistent squad when the agent is not yet in one."""

    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_setup"

    @property
    def description(self) -> str:
        return (
            "Create a persistent multi-agent squad with you as lead.\n"
            "Use this for Discord/community staffing — NOT the team() tool (one-run waves).\n"
            "REQUIRED: ask_user() first to confirm structure with the owner, then call "
            "squad_setup(action='create', owner_confirmed=true, name='...', title='...', "
            "description='lead mission'). After creation use adopt_orchestration_profile("
            "profile='squad_lead') and squad(action='spawn_member', ...) to build the team.\n"
            "CHANNEL TOPOLOGY: ask whether the owner wants SINGLE FACE (your Discord bot only; "
            "members via squad inbox) or MULTI FACE (separate bot token per member on their "
            "Tools → discord-channel). Never reuse your bot token on members."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        props = dict(_SHARED_PARAMS)
        props["action"] = {"type": "string", "enum": ["create"]}
        props["name"] = {"type": "string", "description": "Squad display name."}
        return {
            "type": "object",
            "required": ["action", "name"],
            "properties": props,
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        import json

        kwargs = params
        action = (kwargs.get("action") or "").strip().lower()
        if action != "create":
            return ToolResult(content=f"Unknown squad_setup action: {action}", is_error=True)
        if self._sm.get_squad_for_agent(self._caller) is not None:
            return ToolResult(
                content="You already belong to a squad — use squad() tools instead.",
                is_error=True,
            )
        try:
            result = self._sm.create_squad_for_agent(
                self._caller,
                name=(kwargs.get("name") or "").strip(),
                owner_confirmed=bool(kwargs.get("owner_confirmed", False)),
                title=kwargs.get("title", ""),
                description=kwargs.get("description", ""),
                job_persona=kwargs.get("job_persona", ""),
                job_playbook=kwargs.get("job_playbook", ""),
                default_profile=kwargs.get("default_profile", ""),
            )
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            logger.warning("squad_setup failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)


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
            "Members: inspect, list_inbox, propose.\n"
            "Lead: approve, reject, assign, reassign, resolve_escalation, brief, "
            "checkback, pause/resume squad, add_member, remove_member, pause_member, "
            "resume_member, spawn_member, set_member_job (members), set_lead_job "
            "(your job — owner_confirmed after ask_user), request_trust_change "
            "(owner dashboard approval), request_delete_member, status, list_pending.\n"
            "Discord/Slack: see channel.fleet_topology in context — single public face "
            "(lead bot only) vs multi-face (each speaking agent needs its own bot token "
            "on that agent's Tools integration)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        props = dict(_SHARED_PARAMS)
        props["action"] = {"type": "string", "enum": sorted(_ALL_ACTIONS)}
        props["name"] = {"type": "string", "description": "Unused except spawn context."}
        return {
            "type": "object",
            "required": ["action"],
            "properties": props,
        }

    def _require_lead_for_action(self, action: str, squad_id: str) -> ToolResult | None:
        if action not in _LEAD_ONLY_ACTIONS:
            return None
        squad = self._sm.resolve_squad_for_caller(self._caller, squad_id)
        if squad is None:
            return ToolResult(content="Squad not found — pass squad_id or join a squad", is_error=True)
        if not squad.is_lead(self._caller):
            return ToolResult(
                content=f"Only the squad lead may use squad(action='{action}')",
                is_error=True,
            )
        return None

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        import json

        kwargs = params
        action = (kwargs.get("action") or "").strip().lower()
        if action not in _ALL_ACTIONS:
            return ToolResult(content=f"Unknown squad action: {action}", is_error=True)

        lead_err = self._require_lead_for_action(action, (kwargs.get("squad_id") or "").strip())
        if lead_err is not None:
            return lead_err

        try:
            if action in _ASYNC_ACTIONS:
                result = await self._sm.handle_action_async(
                    self._caller,
                    action,
                    **kwargs,
                )
            else:
                result = self._sm.handle_action(self._caller, action, **_pass_kwargs(kwargs))
            return ToolResult(content=json.dumps(result, indent=2))
        except PermissionError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            logger.warning("squad tool failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)


class SquadEscalateTool:
    """Member → lead escalation (wake lead with structured reason)."""

    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_escalate"

    @property
    def description(self) -> str:
        return (
            "Escalate to your squad lead when blocked by trust, policy, or an incident.\n"
            "Members only — the lead should use squad() or owner channels instead."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short escalation reason (e.g. policy, tool_denied, incident).",
                },
                "context": {
                    "type": "string",
                    "description": "Optional detail for the lead (what happened, what you need).",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        import json

        kwargs = params
        reason = (kwargs.get("reason") or "").strip()
        if not reason:
            return ToolResult(content="reason is required", is_error=True)
        try:
            result = self._sm.escalate(
                self._caller,
                reason=reason,
                context=(kwargs.get("context") or "").strip(),
            )
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            logger.warning("squad_escalate failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)


class SquadMessageTool:
    """Internal squad peer messaging."""

    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_message"

    @property
    def description(self) -> str:
        return (
            "Send an internal note to another squad member or broadcast to the squad.\n"
            "High/urgent priority may wake idle targets. Not a replacement for Discord."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string", "description": "Message body."},
                "to_agent_id": {
                    "type": "string",
                    "description": "Target member agent ID; omit to broadcast to all other members.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["normal", "high", "urgent"],
                    "description": "Delivery priority (high/urgent may wake idle targets).",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        import json

        kwargs = params
        message = (kwargs.get("message") or "").strip()
        if not message:
            return ToolResult(content="message is required", is_error=True)
        try:
            result = self._sm.squad_message(
                self._caller,
                message=message,
                to_agent_id=(kwargs.get("to_agent_id") or "").strip(),
                priority=(kwargs.get("priority") or "normal").strip(),
            )
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            logger.warning("squad_message failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)


class SquadReportDoneTool:
    """Member reports completion of an approved squad todo."""

    def __init__(self, squad_manager: Any, caller_agent_id: str) -> None:
        self._sm = squad_manager
        self._caller = caller_agent_id

    @property
    def name(self) -> str:
        return "squad_report_done"

    @property
    def description(self) -> str:
        return (
            "Mark an approved squad todo complete and notify the lead.\n"
            "Use after finishing work assigned from the squad inbox."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["todo_id"],
            "properties": {
                "todo_id": {"type": "string", "description": "Todo ID to mark done."},
                "squad_id": {
                    "type": "string",
                    "description": "Squad ID (optional if you belong to one).",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        import json

        kwargs = params
        todo_id = (kwargs.get("todo_id") or "").strip()
        if not todo_id:
            return ToolResult(content="todo_id is required", is_error=True)
        try:
            result = self._sm.report_done(
                self._caller,
                todo_id=todo_id,
                squad_id=(kwargs.get("squad_id") or "").strip(),
            )
            return ToolResult(content=json.dumps(result, indent=2))
        except Exception as exc:
            logger.warning("squad_report_done failed: %s", exc, exc_info=True)
            return ToolResult(content=str(exc), is_error=True)