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
    SquadPendingAction,
    SquadRegistry,
)

logger = logging.getLogger(__name__)


def _topology_note_for_lead(lead_agent_id: str) -> str:
    try:
        from server.main import app
        from nls.runtime.fleet_channel_topology import (
            ask_user_topology_questions,
            build_fleet_topology_snapshot,
            render_topology_guidance,
        )

        am = app.state.agent_manager
        rt = am.get_runtime(lead_agent_id) if am else None
        agent_dir = getattr(rt, "agent_dir", None) if rt else None
        if not agent_dir:
            settings = app.state.settings
            agent_dir = settings.agents_dir / lead_agent_id
        snap = build_fleet_topology_snapshot(
            agent_id=lead_agent_id,
            agent_dir=agent_dir,
            app=app,
            planning_fleet=True,
        )
        guidance = render_topology_guidance(snap, compact=True)
        questions = ask_user_topology_questions()
        return (
            f"{guidance}\n\nAsk owner via ask_user(): {questions[0]}"
            if guidance
            else questions[0]
        )
    except Exception:
        from nls.runtime.fleet_channel_topology import ask_user_topology_questions
        return ask_user_topology_questions()[0]


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
        agents_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._data_dir = data_dir
        self._agents_dir = agents_dir
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

    def resolve_squad_for_caller(self, caller_agent_id: str, squad_id: str = "") -> Squad | None:
        """Squad record when caller is a member (by id or membership lookup)."""
        squad = self._registry.get(squad_id) if squad_id else self._registry.get_for_agent(caller_agent_id)
        if squad is None or not squad.is_member(caller_agent_id):
            return None
        return squad

    def sync_agent_runtime(
        self,
        agent_id: str,
        runtime: Any,
        *,
        squad: Squad | None = None,
        lookup_squad: bool = False,
    ) -> None:
        """Refresh Cryptex job/trust, squad tools, and lead WM hooks on a loaded runtime."""
        if runtime is None:
            return
        effective = squad
        if lookup_squad:
            effective = self._registry.get_for_agent(agent_id)
        if hasattr(runtime, "sync_job_trust"):
            runtime.sync_job_trust(squad=effective)
        if hasattr(runtime, "sync_squad_tools"):
            runtime.sync_squad_tools()
        if effective is not None and effective.is_lead(agent_id):
            hooks = getattr(runtime, "_agentic_hooks", None)
            if hooks is not None:
                self.set_hooks(hooks)

    def apply_roster_change(
        self,
        squad: Squad,
        old_member_ids: set[str],
        sync_agent: Callable[[str, Squad | None], None],
    ) -> None:
        """Refresh Cryptex/tools for all affected agents and push roster updates."""
        new_ids = set(squad.all_member_ids)
        removed = old_member_ids - new_ids
        added = new_ids - old_member_ids

        for aid in removed:
            sync_agent(aid, None)
        for aid in new_ids:
            sync_agent(aid, squad)

        if not (added or removed):
            return

        detail_parts: list[str] = []
        if added:
            detail_parts.append(f"Joined: {', '.join(sorted(added))}")
        if removed:
            detail_parts.append(f"Left: {', '.join(sorted(removed))}")
        roster_line = f"Current members: {', '.join(squad.all_member_ids)}"
        prompt = (
            f"[SQUAD ROSTER UPDATE] Squad '{squad.name}' ({squad.id}). "
            f"{'; '.join(detail_parts)}.\n{roster_line}\n"
            "Squad membership context is already refreshed in your system slots."
        )
        source = f"squad_roster:{squad.id}"

        if self._enqueue_dispatch:
            for aid in new_ids:
                self._enqueue_dispatch(aid, prompt, source)
            for aid in removed:
                self._enqueue_dispatch(
                    aid,
                    f"[SQUAD] You are no longer in squad '{squad.name}'. "
                    "Squad tools and membership context have been cleared.",
                    f"squad_roster_left:{squad.id}",
                )

    def require_membership(self, agent_id: str, squad_id: str) -> Squad:
        squad = self._registry.get(squad_id)
        if squad is None:
            raise ValueError(f"Squad {squad_id} not found")
        if not squad.is_member(agent_id):
            raise ValueError(f"Agent {agent_id} is not a member of squad {squad_id}")
        return squad

    def _sync_roster_from_app(self, squad: Squad, old_member_ids: set[str]) -> None:
        try:
            from server.main import app

            am = app.state.agent_manager
            smgr = getattr(app.state, "squad_manager", None)
            if smgr is None:
                return

            def sync_fn(aid: str, sq: Squad | None) -> None:
                runtime = am.get_runtime(aid)
                smgr.sync_agent_runtime(aid, runtime, squad=sq)
                if runtime is not None and hasattr(runtime, "sync_squad_tools"):
                    runtime.sync_squad_tools()

            self.apply_roster_change(squad, old_member_ids, sync_fn)
        except Exception as exc:
            logger.warning("squad roster sync failed: %s", exc)

    def _remove_member_from_squad(self, squad: Squad, agent_id: str) -> Squad:
        old_ids = set(squad.all_member_ids)
        if agent_id not in old_ids:
            raise ValueError(f"Agent {agent_id} is not in this squad")
        if agent_id == squad.lead_agent_id:
            others = [m for m in squad.member_agent_ids if m != agent_id]
            if not others:
                raise ValueError("Cannot remove the only member — delete the squad instead")
            new_lead = others[0]
            new_members = [m for m in squad.member_agent_ids if m not in (agent_id, new_lead)]
            squad = self._registry.update_members(
                squad.id,
                lead_agent_id=new_lead,
                member_agent_ids=new_members,
            )
        else:
            new_members = [m for m in squad.member_agent_ids if m != agent_id]
            squad = self._registry.update_members(
                squad.id,
                member_agent_ids=new_members,
            )
        self._sync_roster_from_app(squad, old_ids)
        return squad

    def _add_member_to_squad(self, squad: Squad, agent_id: str) -> Squad:
        if self._registry.get_for_agent(agent_id) is not None:
            raise ValueError(f"Agent {agent_id} already belongs to a squad")
        if agent_id in squad.all_member_ids:
            return squad
        old_ids = set(squad.all_member_ids)
        members = list(squad.member_agent_ids) + [agent_id]
        squad = self._registry.update_members(squad.id, member_agent_ids=members)
        self._sync_roster_from_app(squad, old_ids)
        return squad

    def _require_lead(self, squad: Squad, caller_agent_id: str) -> None:
        if not squad.is_lead(caller_agent_id):
            raise PermissionError("Only the squad lead may perform this action")

    def _require_owner_confirmed(self, owner_confirmed: bool, *, action: str) -> None:
        if not owner_confirmed:
            raise ValueError(
                f"owner_confirmed=true required for {action} — use ask_user() to get "
                "explicit owner approval in chat first"
            )

    def _agent_dir(self, agent_id: str) -> Path:
        if self._agents_dir is not None:
            return self._agents_dir / agent_id
        from server.main import app

        return app.state.settings.agents_dir / agent_id

    def _sync_runtime_job_trust(self, agent_id: str) -> None:
        try:
            from server.main import app

            am = app.state.agent_manager
            rt = am.get_runtime(agent_id)
            self.sync_agent_runtime(agent_id, rt, lookup_squad=True)
        except Exception as exc:
            logger.debug("job/trust runtime sync skipped for %s: %s", agent_id, exc)

    def _apply_job_patch_fields(self, agent_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        from nls.runtime.job_trust import load_job, save_job

        if not fields:
            raise ValueError("No job fields to apply")
        agent_dir = self._agent_dir(agent_id)
        if not agent_dir.exists():
            raise ValueError(f"Agent {agent_id} not found")
        job = load_job(agent_dir)
        allowed = {
            "title", "mission", "persona", "playbook", "in_scope", "out_of_scope",
            "refusal_template", "refusal_examples", "escalation_paths",
            "default_profile", "strategic_priorities",
        }
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            setattr(job, key, val)
        save_job(agent_dir, job)
        self._sync_runtime_job_trust(agent_id)
        return job.to_dict()

    def _apply_trust_patch_fields(self, agent_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        from nls.runtime.job_trust import ChannelTrustOverlay, load_trust, save_trust

        if not fields:
            raise ValueError("No trust fields to apply")
        agent_dir = self._agent_dir(agent_id)
        if not agent_dir.exists():
            raise ValueError(f"Agent {agent_id} not found")
        trust = load_trust(agent_dir)
        if "channel_overlays" in fields and fields["channel_overlays"] is not None:
            trust.channel_overlays = [
                ChannelTrustOverlay.from_dict(o)
                for o in fields["channel_overlays"]
                if isinstance(o, dict)
            ]
        for key in ("tools_allow", "tools_deny", "action_classes_allow", "action_classes_deny"):
            if key in fields and fields[key] is not None:
                incoming = [str(v).strip() for v in fields[key] if str(v).strip()]
                merged = list(dict.fromkeys([*(getattr(trust, key) or []), *incoming]))
                setattr(trust, key, merged)
        save_trust(agent_dir, trust)
        self._sync_runtime_job_trust(agent_id)
        return trust.to_dict()

    @staticmethod
    def _job_fields_from_kwargs(**kwargs: Any) -> dict[str, Any]:
        mapping = {
            "title": kwargs.get("title") or kwargs.get("job_title"),
            "mission": kwargs.get("description") or kwargs.get("job_mission") or kwargs.get("message"),
            "persona": kwargs.get("job_persona") or kwargs.get("persona"),
            "playbook": kwargs.get("job_playbook") or kwargs.get("playbook"),
            "default_profile": kwargs.get("default_profile"),
            "in_scope": kwargs.get("in_scope"),
            "out_of_scope": kwargs.get("out_of_scope"),
            "refusal_template": kwargs.get("refusal_template"),
            "escalation_paths": kwargs.get("escalation_paths"),
            "strategic_priorities": kwargs.get("strategic_priorities"),
        }
        return {k: v for k, v in mapping.items() if v is not None and v != ""}

    @staticmethod
    def _trust_fields_from_kwargs(**kwargs: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("tools_allow", "tools_deny", "action_classes_allow", "action_classes_deny"):
            val = kwargs.get(key)
            if val is not None:
                out[key] = val
        overlays = kwargs.get("channel_overlays")
        if overlays is not None:
            out["channel_overlays"] = overlays
        return out

    def create_squad_for_agent(
        self,
        caller_agent_id: str,
        *,
        name: str,
        owner_confirmed: bool = False,
        **job_kwargs: Any,
    ) -> dict[str, Any]:
        """Bootstrap a squad with caller as lead (requires prior owner confirmation)."""
        self._require_owner_confirmed(owner_confirmed, action="squad_setup create")
        if self._registry.get_for_agent(caller_agent_id):
            raise ValueError("You already belong to a squad")
        squad = self._registry.create(
            name=(name or "Squad").strip() or "Squad",
            lead_agent_id=caller_agent_id,
            member_agent_ids=[],
        )
        job_fields = self._job_fields_from_kwargs(**job_kwargs)
        if job_fields:
            if not job_fields.get("default_profile"):
                job_fields["default_profile"] = "squad_lead"
            self._apply_job_patch_fields(caller_agent_id, job_fields)
        old_ids: set[str] = set()
        self._sync_roster_from_app(squad, old_ids)
        try:
            from server.main import app

            am = app.state.agent_manager
            rt = am.get_runtime(caller_agent_id)
            if rt is not None and hasattr(rt, "sync_squad_tools"):
                rt.sync_squad_tools()
                if hasattr(rt, "refresh_tools"):
                    rt.refresh_tools()
        except Exception as exc:
            logger.debug("post-create squad tool sync: %s", exc)
        return {
            "squad": squad.to_dict(),
            "role": "lead",
            "note": (
                "Squad created. Call adopt_orchestration_profile(profile='squad_lead'). "
                "Use spawn_member to add agents, set_member_job to refine member charters."
            ),
            "channel_topology": _topology_note_for_lead(caller_agent_id),
        }

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
        owner_confirmed: bool = False,
        job_persona: str = "",
        job_playbook: str = "",
        default_profile: str = "",
        in_scope: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
        action_classes_allow: list[str] | None = None,
        action_classes_deny: list[str] | None = None,
        channel_overlays: list[dict[str, Any]] | None = None,
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

        if action in ("remove_member", "disband_member"):
            self._require_lead(squad, caller_agent_id)
            target = (target_agent_id or assignee_agent_id).strip()
            if not target:
                raise ValueError("target_agent_id required")
            squad = self._remove_member_from_squad(squad, target)
            return {"removed": target, "squad": squad.to_dict()}

        if action == "add_member":
            self._require_lead(squad, caller_agent_id)
            target = (target_agent_id or assignee_agent_id).strip()
            if not target:
                raise ValueError("target_agent_id required")
            squad = self._add_member_to_squad(squad, target)
            return {"added": target, "squad": squad.to_dict()}

        if action == "request_delete_member":
            self._require_lead(squad, caller_agent_id)
            target = (target_agent_id or assignee_agent_id).strip()
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            sole_member = len(squad.all_member_ids) <= 1
            for existing in squad.pending_actions:
                if (
                    existing.status == "pending"
                    and existing.action_type == "delete_agent"
                    and existing.target_agent_id == target
                ):
                    raise ValueError(f"Delete request for {target} is already pending owner approval")
            pending = SquadPendingAction(
                action_type="delete_agent",
                target_agent_id=target,
                requested_by=caller_agent_id,
                title=title.strip() or f"Delete {target}",
                description=(message or description or reason).strip(),
                delete_squad_on_approve=sole_member,
            )
            squad.pending_actions.append(pending)
            self._registry.save(squad)
            note = "Owner must approve in the dashboard before the agent is deleted."
            if sole_member:
                note += " Approving will also disband the squad."
            return {
                "pending_action": pending.to_dict(),
                "note": note,
            }

        if action == "list_pending":
            self._require_lead(squad, caller_agent_id)
            pending = [p.to_dict() for p in squad.pending_actions if p.status == "pending"]
            return {"pending_actions": pending}

        if action == "set_member_job":
            self._require_lead(squad, caller_agent_id)
            target = (target_agent_id or assignee_agent_id).strip()
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            if target == caller_agent_id:
                raise ValueError(
                    "Use set_lead_job with owner_confirmed=true after ask_user() for your own job"
                )
            job_fields = self._job_fields_from_kwargs(
                title=title,
                description=description,
                message=message,
                job_persona=job_persona,
                job_playbook=job_playbook,
                default_profile=default_profile,
                in_scope=in_scope,
                out_of_scope=out_of_scope,
            )
            if not job_fields:
                raise ValueError("Provide title and/or description (mission) for the member job")
            job = self._apply_job_patch_fields(target, job_fields)
            return {"agent_id": target, "job": job}

        if action == "set_lead_job":
            self._require_lead(squad, caller_agent_id)
            self._require_owner_confirmed(owner_confirmed, action="set_lead_job")
            job_fields = self._job_fields_from_kwargs(
                title=title,
                description=description,
                message=message,
                job_persona=job_persona,
                job_playbook=job_playbook,
                default_profile=default_profile or "squad_lead",
            )
            if not job_fields:
                raise ValueError("Provide title and/or description for your lead job")
            job = self._apply_job_patch_fields(caller_agent_id, job_fields)
            return {"agent_id": caller_agent_id, "job": job}

        if action == "request_trust_change":
            self._require_lead(squad, caller_agent_id)
            target = (target_agent_id or assignee_agent_id or caller_agent_id).strip()
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            trust_fields = self._trust_fields_from_kwargs(
                tools_allow=tools_allow,
                tools_deny=tools_deny,
                action_classes_allow=action_classes_allow,
                action_classes_deny=action_classes_deny,
                channel_overlays=channel_overlays,
            )
            if not trust_fields:
                raise ValueError(
                    "Provide trust fields (tools_allow, tools_deny, channel_overlays, etc.)"
                )
            for existing in squad.pending_actions:
                if (
                    existing.status == "pending"
                    and existing.action_type == "patch_trust"
                    and existing.target_agent_id == target
                ):
                    raise ValueError(
                        f"Trust change for {target} is already pending owner approval"
                    )
            pending = SquadPendingAction(
                action_type="patch_trust",
                target_agent_id=target,
                requested_by=caller_agent_id,
                title=title.strip() or f"Update trust for {target}",
                description=(message or description or reason).strip(),
                payload={"trust": trust_fields},
            )
            squad.pending_actions.append(pending)
            self._registry.save(squad)
            return {
                "pending_action": pending.to_dict(),
                "note": "Owner must approve trust changes in the dashboard.",
            }

        raise ValueError(f"Unknown squad action: {action}")

    async def handle_action_async(
        self,
        caller_agent_id: str,
        action: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        action = (action or "").strip().lower()
        squad_id = kwargs.get("squad_id", "")
        squad = self._registry.get(squad_id) if squad_id else self._registry.get_for_agent(caller_agent_id)
        if squad is None:
            raise ValueError("Squad not found — pass squad_id or join a squad")
        self.require_membership(caller_agent_id, squad.id)
        self._require_lead(squad, caller_agent_id)

        target = (kwargs.get("target_agent_id") or kwargs.get("assignee_agent_id") or "").strip()
        title = kwargs.get("title", "")
        description = kwargs.get("description", "")
        message = kwargs.get("message", "")

        if action == "pause_member":
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            await self._pause_agent(target)
            return {"paused": target}

        if action == "resume_member":
            if not target or not squad.is_member(target):
                raise ValueError("target_agent_id must be a squad member")
            await self._unpause_agent(target)
            return {"resumed": target}

        if action == "spawn_member":
            return await self._spawn_member(
                squad,
                caller_agent_id,
                name=title.strip() or "Squad member",
                job_title=title.strip() or "Squad member",
                mission=(description or message).strip(),
                genesis_version=(kwargs.get("genesis_version") or "").strip(),
            )

        raise ValueError(f"Unknown async squad action: {action}")

    async def _pause_agent(self, agent_id: str) -> None:
        from server.main import app

        cs = getattr(app.state, "consciousness_scheduler", None)
        if cs is None:
            raise RuntimeError("Consciousness scheduler not available")
        ok = await cs.pause_agent(agent_id)
        if not ok:
            raise ValueError(f"Failed to pause agent {agent_id}")

    async def _unpause_agent(self, agent_id: str) -> None:
        from server.main import app

        cs = getattr(app.state, "consciousness_scheduler", None)
        am = app.state.agent_manager
        if cs is None:
            raise RuntimeError("Consciousness scheduler not available")
        ok = await cs.unpause_agent(agent_id)
        if not ok:
            raise ValueError(f"Failed to resume agent {agent_id}")
        if am.get_runtime(agent_id) is None:
            await am.load_agent(agent_id)

    async def _spawn_member(
        self,
        squad: Squad,
        lead_agent_id: str,
        *,
        name: str,
        job_title: str,
        mission: str,
        genesis_version: str = "",
    ) -> dict[str, Any]:
        from server.main import app
        from nls.runtime.job_trust import load_job, save_job

        am = app.state.agent_manager
        settings = app.state.settings
        genesis = genesis_version or settings.default_genesis

        meta = await am.create_agent(
            genesis_version=genesis,
            name=name,
            soul_wish=mission or None,
        )
        new_id = meta["agent_id"]
        try:
            agent_dir = settings.agents_dir / new_id
            job = load_job(agent_dir)
            job.title = job_title
            if mission:
                job.mission = mission
            job.default_profile = job.default_profile or "solo_structured"
            save_job(agent_dir, job)

            squad = self._add_member_to_squad(squad, new_id)

            rt = am.get_runtime(new_id)
            if rt is None:
                await am.load_agent(new_id)
                rt = am.get_runtime(new_id)
            self.sync_agent_runtime(new_id, rt, squad=squad)
            if rt is not None and hasattr(rt, "sync_squad_tools"):
                rt.sync_squad_tools()

            brief_msg = mission or f"You are {job_title} on squad '{squad.name}'. Report to lead {lead_agent_id}."
            self.handle_action(
                lead_agent_id,
                "brief",
                squad_id=squad.id,
                target_agent_id=new_id,
                message=brief_msg,
            )
        except Exception:
            try:
                await am.delete_agent(new_id)
            except Exception as cleanup_exc:
                logger.warning("spawn_member cleanup failed for %s: %s", new_id, cleanup_exc)
            raise

        return {
            "agent_id": new_id,
            "name": name,
            "job_title": job_title,
            "squad_id": squad.id,
            "brief": brief_msg,
            "channel_setup": (
                "This member has no Discord/Slack yet. SINGLE FACE: they work via "
                "squad inbox only. MULTI FACE: owner links discord-channel on this "
                f"agent ({new_id}) in Tools with a NEW bot token — never copy the lead's."
            ),
        }

    async def resolve_pending_action(
        self,
        squad_id: str,
        action_id: str,
        *,
        approved: bool,
        resolution_note: str = "",
    ) -> dict[str, Any]:
        squad = self._registry.get(squad_id)
        if squad is None:
            raise ValueError("Squad not found")
        pending = next((p for p in squad.pending_actions if p.id == action_id), None)
        if pending is None:
            raise ValueError(f"Pending action {action_id} not found")
        if pending.status != "pending":
            raise ValueError(f"Action already {pending.status}")

        if not approved:
            pending.status = "rejected"
            pending.resolved_at = time.time()
            pending.resolution_note = resolution_note.strip() or "denied by owner"
            self._registry.save(squad)
            return {"pending_action": pending.to_dict()}

        if pending.action_type == "delete_agent":
            from server.main import app

            target = pending.target_agent_id
            if not target:
                raise ValueError("Pending delete action missing target_agent_id")
            am = app.state.agent_manager
            disband = pending.delete_squad_on_approve or len(squad.all_member_ids) <= 1

            if disband:
                for aid in list(squad.all_member_ids):
                    rt = am.get_runtime(aid)
                    self.sync_agent_runtime(aid, rt, squad=None)
            elif squad.is_member(target):
                squad = self._remove_member_from_squad(squad, target)
                squad = self._registry.get(squad_id)
                if squad is None:
                    raise ValueError("Squad not found after roster update")
                pending = next((p for p in squad.pending_actions if p.id == action_id), None)
                if pending is None:
                    raise ValueError(f"Pending action {action_id} not found after roster update")

            pending.status = "approved"
            pending.resolved_at = time.time()
            pending.resolution_note = resolution_note.strip() or "deleted by owner approval"

            if disband:
                self._registry.delete(squad_id)
                await am.delete_agent(target)
                return {
                    "pending_action": pending.to_dict(),
                    "deleted": target,
                    "squad_deleted": squad_id,
                }

            self._registry.save(squad)
            await am.delete_agent(target)
            return {"pending_action": pending.to_dict(), "deleted": target}

        if pending.action_type == "patch_job":
            target = pending.target_agent_id
            if not target:
                raise ValueError("Pending job patch missing target_agent_id")
            job_fields = (pending.payload or {}).get("job") or {}
            job = self._apply_job_patch_fields(target, job_fields)
            pending.status = "approved"
            pending.resolved_at = time.time()
            pending.resolution_note = resolution_note.strip() or "job updated by owner approval"
            self._registry.save(squad)
            return {"pending_action": pending.to_dict(), "job": job}

        if pending.action_type == "patch_trust":
            target = pending.target_agent_id
            if not target:
                raise ValueError("Pending trust patch missing target_agent_id")
            trust_fields = (pending.payload or {}).get("trust") or {}
            trust = self._apply_trust_patch_fields(target, trust_fields)
            pending.status = "approved"
            pending.resolved_at = time.time()
            pending.resolution_note = resolution_note.strip() or "trust updated by owner approval"
            self._registry.save(squad)
            return {"pending_action": pending.to_dict(), "trust": trust}

        pending.status = "approved"
        pending.resolved_at = time.time()
        pending.resolution_note = resolution_note.strip()
        self._registry.save(squad)
        return {"pending_action": pending.to_dict()}

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
