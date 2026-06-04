"""Squad lifecycle manager — persistent multi-agent coordination."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from .squad_registry import (
    INBOX_STATUSES,
    Squad,
    SquadEscalation,
    SquadInboxItem,
    SquadRegistry,
)

logger = logging.getLogger(__name__)


def format_squad_wake_prompt(squad: Squad, *, kind: str, detail: str = "") -> str:
    pending = [i for i in squad.inbox if i.status == "proposed"]
    open_esc = [e for e in squad.escalations if e.status == "open"]
    lines = [
        f"[SQUAD {kind.upper()}] Squad '{squad.name}' ({squad.id})",
        f"Lead: {squad.lead_agent_id}",
        f"Members: {', '.join(squad.member_agent_ids)}",
    ]
    if pending:
        lines.append(f"Inbox pending approval: {len(pending)}")
        for item in pending[:5]:
            lines.append(f"  - [{item.id}] {item.title} → {item.suggested_assignee_id or '?'}")
    if open_esc:
        lines.append(f"Open escalations: {len(open_esc)}")
        for esc in open_esc[:5]:
            lines.append(f"  - {esc.member_agent_id}: {esc.reason[:80]}")
    if detail:
        lines.append(detail)
    lines.append(
        "Use squad(action='inspect'|'list_inbox'|'approve'|'assign'|...) to coordinate. "
        "Resolve escalations before completing this wake."
    )
    return "\n".join(lines)


class SquadManager:
    def __init__(
        self,
        registry: SquadRegistry,
        *,
        data_dir: Path,
        agent_manager: Any = None,
        get_runtime: Callable[[str], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._data_dir = data_dir
        self._agent_manager = agent_manager
        self._get_runtime = get_runtime
        self._enqueue_dispatch: Callable[[str, str, str], None] | None = None
        self._drain_dispatch: Callable[[str, str], int] | None = None
        self._hooks: Any | None = None

    def set_hooks(self, hooks: Any) -> None:
        """Loop hooks from squad lead runtime (WM orchestration ring)."""
        self._hooks = hooks

    @staticmethod
    def _wm_team_key(squad_id: str) -> str:
        return f"squad:{squad_id}"

    def _member_idx(self, squad: Squad, agent_id: str) -> int:
        ids = squad.all_member_ids
        try:
            return ids.index(agent_id)
        except ValueError:
            return -1

    def _wm_add_escalation(self, squad: Squad, member_agent_id: str, context: str) -> None:
        if self._hooks is None:
            return
        idx = self._member_idx(squad, member_agent_id)
        if idx < 0:
            return
        fn = getattr(self._hooks, "wm_orch_add_escalation", None)
        if fn:
            try:
                fn(self._wm_team_key(squad.id), idx, context[:200])
            except Exception:
                logger.debug("squad wm_orch_add_escalation failed", exc_info=True)

    def _wm_resolve_escalation(
        self,
        squad: Squad,
        member_agent_id: str,
        outcome: str,
    ) -> None:
        if self._hooks is None:
            return
        idx = self._member_idx(squad, member_agent_id)
        if idx < 0:
            return
        fn = getattr(self._hooks, "wm_orch_resolve_escalation", None)
        if fn:
            try:
                fn(self._wm_team_key(squad.id), idx, outcome[:200])
            except Exception:
                logger.debug("squad wm_orch_resolve_escalation failed", exc_info=True)

    def build_kanban_view(self, squad: Squad) -> dict[str, Any]:
        """Aggregated squad board: inbox + per-member squad todos."""
        inbox = {
            "proposed": [i.to_dict() for i in squad.inbox if i.status == "proposed"],
            "approved": [i.to_dict() for i in squad.inbox if i.status == "approved"],
            "rejected": [i.to_dict() for i in squad.inbox if i.status == "rejected"],
        }
        members: dict[str, list[dict[str, Any]]] = {}
        tm = self._get_todo_manager()
        if tm:
            for mid in squad.all_member_ids:
                store = tm.get_store(mid)
                members[mid] = [
                    t.to_dict() for t in store.list_items()
                    if getattr(t, "squad_id", "") == squad.id
                ]
        return {
            "squad_id": squad.id,
            "inbox": inbox,
            "member_todos": members,
            "open_escalations": [
                e.to_dict() for e in squad.escalations if e.status == "open"
            ],
        }

    def set_dispatch_hooks(
        self,
        enqueue: Callable[[str, str, str], None] | None,
        drain: Callable[[str, str], int] | None = None,
    ) -> None:
        """enqueue(agent_id, prompt, source) — drain(lead_agent_id, source_exact)."""
        self._enqueue_dispatch = enqueue
        self._drain_dispatch = drain

    def _wake_lead(self, squad: Squad, kind: str, detail: str = "") -> None:
        if not self._enqueue_dispatch or not squad.lead_agent_id:
            return
        prompt = format_squad_wake_prompt(squad, kind=kind, detail=detail)
        source = f"squad_{kind}:{squad.id}"
        self._enqueue_dispatch(squad.lead_agent_id, prompt, source)

    def get_squad_for_agent(self, agent_id: str) -> Squad | None:
        return self._registry.get_for_agent(agent_id)

    def require_membership(self, agent_id: str, squad_id: str) -> Squad:
        squad = self._registry.get(squad_id)
        if squad is None:
            raise ValueError(f"Squad {squad_id} not found")
        if not squad.is_member(agent_id):
            raise ValueError(f"Agent {agent_id} is not a member of squad {squad_id}")
        return squad

    def build_checkback_detail(self, squad: Squad) -> str:
        """Extra lines for scheduled checkback wakes (member todo health)."""
        lines: list[str] = []
        pending = [i for i in squad.inbox if i.status == "proposed"]
        if pending:
            lines.append(f"{len(pending)} inbox item(s) awaiting your approval.")
        open_esc = [e for e in squad.escalations if e.status == "open"]
        if open_esc:
            lines.append(f"{len(open_esc)} open escalation(s) need resolution.")

        tm = self._get_todo_manager()
        if tm is None:
            return "\n".join(lines)

        now = time.time()
        stale_threshold = 2 * 3600
        for mid in squad.member_agent_ids:
            if mid == squad.lead_agent_id:
                continue
            store = tm.get_store(mid)
            squad_todos = [
                t for t in store.list_items()
                if getattr(t, "squad_id", "") == squad.id
            ]
            in_prog = [t for t in squad_todos if t.status == "in_progress"]
            queued = [t for t in squad_todos if t.status in ("queued", "inbox")]
            stale = [
                t for t in in_prog
                if (now - (t.updated_at or t.created_at or now)) > stale_threshold
            ]
            if in_prog or queued or stale:
                parts = []
                if queued:
                    parts.append(f"{len(queued)} queued")
                if in_prog:
                    parts.append(f"{len(in_prog)} in progress")
                if stale:
                    parts.append(f"{len(stale)} stale >2h")
                lines.append(f"Member {mid}: " + ", ".join(parts))
        if not lines:
            lines.append("Periodic health check — squad(action='status'|'inspect').")
        return "\n".join(lines)

    def build_squad_context_block(self, agent_id: str) -> str:
        squad = self._registry.get_for_agent(agent_id)
        if squad is None:
            return ""
        if squad.is_lead(agent_id):
            peers = [m for m in squad.member_agent_ids if m != agent_id]
            return (
                f"SQUAD LEAD: You lead squad '{squad.name}' ({squad.id}).\n"
                f"Members: {', '.join(peers) or '(none)'}\n"
                "Coordinate via the squad tool: approve inbox items, assign work, "
                "resolve squad_escalate requests. You may speak for the owner on policy."
            )
        return (
            f"SQUAD MEMBER: You are in squad '{squad.name}' ({squad.id}).\n"
            f"Lead: {squad.lead_agent_id}\n"
            f"Peers: {', '.join(m for m in squad.all_member_ids if m != agent_id)}\n"
            "Use squad(action='propose') for shared inbox; squad_escalate to reach your lead."
        )

    def _get_todo_manager(self) -> Any | None:
        try:
            from server.main import app

            tm = getattr(app.state, "todo_manager", None)
            if tm is not None:
                return tm
            loader = getattr(app.state, "skill_loader", None)
            if loader is None:
                return None
            skill = loader.skills.get("todo-list")
            if skill is None:
                return None
            ctx = getattr(skill, "context", None)
            return getattr(ctx, "adapter", None) if ctx else None
        except Exception:
            return None

    def _approve_inbox_item(
        self,
        squad: Squad,
        item: SquadInboxItem,
        *,
        assignee_id: str,
        idle_eligible: bool = True,
    ) -> str:
        assignee = assignee_id or item.suggested_assignee_id
        if not assignee or not squad.is_member(assignee):
            raise ValueError("Valid assignee_agent_id required (squad member)")
        tm = self._get_todo_manager()
        if tm is None:
            raise RuntimeError("Todo manager not available")
        store = tm.get_store(assignee)
        todo = store.add(
            title=item.title,
            description=item.description,
            priority=item.priority,
            status="queued",
            idle_eligible=idle_eligible,
            source="squad",
            tags=[*(item.tags or []), f"squad:{squad.id}"],
            squad_id=squad.id,
            squad_inbox_id=item.id,
            assigner_agent_id=squad.lead_agent_id,
            assignee_agent_id=assignee,
        )
        item.status = "approved"
        item.assignee_agent_id = assignee
        item.member_todo_id = todo.id
        item.updated_at = time.time()
        tm.sync_idle_intention(assignee)
        if self._enqueue_dispatch:
            wake_src = f"squad_wake:{assignee}"
            self._enqueue_dispatch(
                assignee,
                f"[SQUAD WORK] Todo [{todo.id}]: {todo.title}\n{item.description or ''}",
                wake_src,
            )
        return todo.id

    def handle_action(
        self,
        caller_agent_id: str,
        action: str,
        *,
        squad_id: str = "",
        item_id: str = "",
        assignee_agent_id: str = "",
        title: str = "",
        description: str = "",
        priority: str = "normal",
        idle_eligible: bool = True,
        tags: list[str] | None = None,
        reason: str = "",
        reject_reason: str = "",
        target_agent_id: str = "",
        message: str = "",
    ) -> dict[str, Any]:
        action = (action or "").strip().lower()
        squad = self._registry.get(squad_id) if squad_id else self._registry.get_for_agent(caller_agent_id)
        if squad is None:
            raise ValueError("Squad not found — pass squad_id or join a squad")

        self.require_membership(caller_agent_id, squad.id)

        if action == "inspect":
            return {"squad": squad.to_dict(), "role": "lead" if squad.is_lead(caller_agent_id) else "member"}

        if action == "list_inbox":
            status_filter = reason if reason in INBOX_STATUSES else ""
            items = squad.inbox
            if status_filter:
                items = [i for i in items if i.status == status_filter]
            return {"inbox": [i.to_dict() for i in items]}

        if action == "propose":
            if squad.paused:
                raise ValueError("Squad is paused")
            item = SquadInboxItem(
                title=title.strip() or "Untitled",
                description=description.strip(),
                priority=priority or "normal",
                suggested_assignee_id=assignee_agent_id.strip(),
                proposer_agent_id=caller_agent_id,
                tags=list(tags or []),
                source="lead" if squad.is_lead(caller_agent_id) else "member",
            )
            squad.inbox.append(item)
            self._registry.save(squad)
            if squad.is_lead(caller_agent_id):
                pass
            else:
                self._wake_lead(squad, "inbox", f"New proposal [{item.id}]: {item.title}")
            return {"item": item.to_dict(), "status": "proposed"}

        if action in ("approve", "reject", "assign", "reassign"):
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may approve, reject, or assign")

        if action == "approve":
            item = next((i for i in squad.inbox if i.id == item_id), None)
            if item is None:
                raise ValueError(f"Inbox item {item_id} not found")
            if item.status != "proposed":
                raise ValueError(f"Item status is {item.status}, not proposed")
            todo_id = self._approve_inbox_item(
                squad, item,
                assignee_id=assignee_agent_id or item.suggested_assignee_id,
                idle_eligible=idle_eligible,
            )
            self._registry.save(squad)
            return {"item": item.to_dict(), "member_todo_id": todo_id}

        if action == "reject":
            item = next((i for i in squad.inbox if i.id == item_id), None)
            if item is None:
                raise ValueError(f"Inbox item {item_id} not found")
            item.status = "rejected"
            item.reject_reason = reject_reason.strip()
            item.updated_at = time.time()
            self._registry.save(squad)
            return {"item": item.to_dict()}

        if action == "assign":
            item = SquadInboxItem(
                title=title.strip() or "Assigned task",
                description=description.strip(),
                priority=priority or "normal",
                suggested_assignee_id=assignee_agent_id,
                proposer_agent_id=caller_agent_id,
                tags=list(tags or []),
                source="owner_via_lead" if squad.is_lead(caller_agent_id) else "lead",
            )
            squad.inbox.append(item)
            todo_id = self._approve_inbox_item(
                squad, item,
                assignee_id=assignee_agent_id,
                idle_eligible=idle_eligible,
            )
            self._registry.save(squad)
            return {"item": item.to_dict(), "member_todo_id": todo_id}

        if action == "checkback":
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may run checkback")
            self._wake_lead(squad, "checkback", "Manual checkback requested.")
            squad.last_checkback_at = time.time()
            self._registry.save(squad)
            return {"ok": True, "squad_id": squad.id}

        if action == "resolve_escalation":
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may resolve escalations")
            esc = next((e for e in squad.escalations if e.id == item_id), None)
            if esc is None:
                raise ValueError(f"Escalation {item_id} not found")
            esc.status = "resolved"
            esc.resolved_at = time.time()
            esc.resolution = (message or reject_reason or "resolved").strip()[:2000]
            self._wm_resolve_escalation(
                squad, esc.member_agent_id, esc.resolution,
            )
            self._registry.save(squad)
            return {"escalation": esc.to_dict()}

        if action == "reassign":
            item = next((i for i in squad.inbox if i.id == item_id), None)
            if item is None or not item.member_todo_id:
                raise ValueError("Approved inbox item with member todo required")
            new_assignee = (assignee_agent_id or "").strip()
            if not new_assignee or not squad.is_member(new_assignee):
                raise ValueError("assignee_agent_id must be a squad member")
            old_assignee = item.assignee_agent_id or item.suggested_assignee_id
            tm = self._get_todo_manager()
            if tm is None:
                raise RuntimeError("Todo manager not available")
            if old_assignee and old_assignee != new_assignee:
                old_store = tm.get_store(old_assignee)
                old_todo = old_store.get(item.member_todo_id)
                if old_todo is None:
                    raise ValueError(f"Todo {item.member_todo_id} not found on {old_assignee}")
                new_store = tm.get_store(new_assignee)
                new_todo = new_store.add(
                    title=old_todo.title,
                    description=old_todo.description,
                    priority=old_todo.priority,
                    status=old_todo.status,
                    idle_eligible=old_todo.idle_eligible,
                    source="squad",
                    tags=list(old_todo.tags or []),
                    squad_id=squad.id,
                    squad_inbox_id=item.id,
                    assigner_agent_id=squad.lead_agent_id,
                    assignee_agent_id=new_assignee,
                )
                old_store.remove(item.member_todo_id)
                item.member_todo_id = new_todo.id
                tm.sync_idle_intention(old_assignee)
            item.assignee_agent_id = new_assignee
            item.suggested_assignee_id = new_assignee
            item.updated_at = time.time()
            self._registry.save(squad)
            tm.sync_idle_intention(new_assignee)
            if self._enqueue_dispatch:
                self._enqueue_dispatch(
                    new_assignee,
                    f"[SQUAD REASSIGNED] {item.title}\n{item.description or ''}",
                    f"squad_wake:{new_assignee}",
                )
            return {"item": item.to_dict(), "member_todo_id": item.member_todo_id}

        if action == "pause":
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may pause the squad")
            squad.paused = True
            self._registry.save(squad)
            return {"paused": True}

        if action == "resume":
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may resume the squad")
            squad.paused = False
            self._registry.save(squad)
            return {"paused": False}

        if action == "status":
            tm = self._get_todo_manager()
            member_status: dict[str, Any] = {}
            if tm:
                for mid in squad.all_member_ids:
                    if mid == squad.lead_agent_id:
                        continue
                    store = tm.get_store(mid)
                    squad_todos = [
                        t.to_dict() for t in store.list_items()
                        if getattr(t, "squad_id", "") == squad.id
                    ]
                    member_status[mid] = {"todos": squad_todos}
            pending = len([i for i in squad.inbox if i.status == "proposed"])
            return {
                "squad_id": squad.id,
                "pending_inbox": pending,
                "open_escalations": len([e for e in squad.escalations if e.status == "open"]),
                "members": member_status,
            }

        if action == "brief":
            if not squad.is_lead(caller_agent_id):
                raise PermissionError("Only the squad lead may brief members")
            target = target_agent_id or assignee_agent_id
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            rt = self._get_runtime(target) if self._get_runtime else None
            if rt is None:
                raise RuntimeError(f"Agent runtime {target} not loaded")
            wm = getattr(rt, "working_memory", None)
            if wm is not None and hasattr(wm, "upsert_orchestration_slot"):
                wm.upsert_orchestration_slot(
                    domain=f"Squad.Brief.{squad.id}",
                    content=message.strip() or description.strip(),
                    access="session",
                    source="squad_lead",
                    salience=0.98,
                )
            if self._enqueue_dispatch:
                self._enqueue_dispatch(
                    target,
                    f"[SQUAD BRIEF] From lead {caller_agent_id}:\n{message or description}",
                    f"squad_wake:{target}",
                )
            return {"briefed": target}

        raise ValueError(f"Unknown squad action: {action}")

    def escalate(
        self,
        member_agent_id: str,
        *,
        reason: str,
        context: str = "",
    ) -> dict[str, Any]:
        squad = self._registry.get_for_agent(member_agent_id)
        if squad is None:
            raise ValueError("Agent is not in a squad")
        if squad.is_lead(member_agent_id):
            raise ValueError("Lead should use owner channel or squad tools, not squad_escalate")
        esc = SquadEscalation(
            member_agent_id=member_agent_id,
            reason=(reason or "escalate").strip(),
            context=(context or "").strip()[:4000],
        )
        squad.escalations.append(esc)
        self._registry.save(squad)
        self._registry.append_message(
            squad.id,
            {"type": "escalation", "from": member_agent_id, "reason": esc.reason},
        )
        self._wm_add_escalation(
            squad,
            member_agent_id,
            f"{esc.reason}: {esc.context[:150]}",
        )
        self._wake_lead(
            squad,
            "escalation",
            f"Escalation from {member_agent_id}: {esc.reason}\n{esc.context[:500]}",
        )
        return {"escalation": esc.to_dict(), "squad_id": squad.id}

    def report_done(
        self,
        member_agent_id: str,
        *,
        todo_id: str,
        squad_id: str = "",
    ) -> dict[str, Any]:
        squad = self._registry.get(squad_id) if squad_id else self._registry.get_for_agent(member_agent_id)
        if squad is None:
            raise ValueError("Squad not found")
        self.require_membership(member_agent_id, squad.id)
        tm = self._get_todo_manager()
        if tm and todo_id:
            store = tm.get_store(member_agent_id)
            item = store.get(todo_id)
            if item and getattr(item, "squad_id", "") == squad.id:
                store.update(todo_id, status="done")
        self._wake_lead(
            squad,
            "item_done",
            f"Member {member_agent_id} completed todo {todo_id or '(unknown)'}.",
        )
        return {"ok": True}

    def squad_message(
        self,
        from_agent_id: str,
        *,
        message: str,
        to_agent_id: str = "",
        priority: str = "normal",
    ) -> dict[str, Any]:
        squad = self._registry.get_for_agent(from_agent_id)
        if squad is None:
            raise ValueError("Agent is not in a squad")
        body = (message or "").strip()
        if not body:
            raise ValueError("message required")
        record = {
            "type": "message",
            "from": from_agent_id,
            "to": to_agent_id or "*",
            "message": body,
            "priority": priority,
        }
        self._registry.append_message(squad.id, record)
        targets = [to_agent_id] if to_agent_id else [
            m for m in squad.all_member_ids if m != from_agent_id
        ]
        for tid in targets:
            if not squad.is_member(tid):
                continue
            if self._enqueue_dispatch and priority in ("high", "urgent"):
                self._enqueue_dispatch(
                    tid,
                    f"[SQUAD MESSAGE] From {from_agent_id}:\n{body}",
                    f"squad_wake:{tid}",
                )
        return {"delivered_to": targets}
