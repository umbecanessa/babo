"""Plan tool -- structured project plan management.

Architecture (orchestrator model):

- The **orchestrator** (you in the main agentic loop) owns a **master plan**:
  one active plan JSON under ``.plans/plan_{id}.json`` — the source of truth
  for top-level steps and status.
- A complex step may spawn a **sub-plan**: another JSON file, linked from the
  master plan via ``sub_plan``. Sub-plans are first-class structured plans,
  not freeform notes.
- Each **sub-plan** is the right unit to hand to **delegate** (sub-agent):
  give the sub-agent a clear task derived from that sub-plan; the sub-agent
  executes and returns; the **orchestrator** reads results, updates the master
  plan / sub-plan steps, and continues.

Use the **todo** / task-board skill for user-visible checklist items on the
Tasks page; use **plan** for structured multi-step runbooks and recovery.
Avoid duplicating the same steps in both unless the plan is macro-scope and
todos are a short execution slice.

Actions:
    create   -- Create a new plan with title, requirements, tech_stack, steps
    read     -- Read the current active plan
    update   -- Update a step's status or notes
    set_requirements -- Set or replace the plan's full requirements text
    set_tech_stack   -- Set or replace structured tech_stack (mandatory stack lock-in)
    add_step -- Add a new step to an existing plan
    sub_plan -- Create a linked sub-plan for a complex step
    verify   -- Trigger verification audit against acceptance criteria
    complete -- Mark plan as done, sync linked todo
    delete   -- Archive a stale/wrong plan (cancels linked teams)
    fix_dependencies -- Validate/repair depends_on graph (inference + cycle break)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time as _time
from pathlib import Path
from typing import Any

from .base import ToolResult
from nls.agentic.breadcrumbs import tool_description_supplement
from nls.agentic.plan_store import Plan, PlanStep, PlanStore

logger = logging.getLogger(__name__)


class PlanTool:
    """Manage structured execution plans in the agent's workspace.

    Parameters
    ----------
    workspace : str
        Absolute path to the agent's workspace directory.
    """

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._store = PlanStore(workspace)
        self._inference_fn: Any | None = None
        self._dep_inference_fn: Any | None = None
        # Track which tools the agent actually called (set by agentic loop)
        self._invoked_tools: set[str] | None = None
        # SKILL ONBOARDING text for required-tool extraction (set by runtime)
        self._onboarding_context: str | None = None
        # Optional async callback to auto-complete a linked todo item.
        # Signature: async fn(todo_id: str) -> None
        self._todo_complete_fn: Any | None = None
        # Optional callback to reset the orchestrator CWD back to workspace
        # root when a plan completes.  Same signature as _cwd_switch_fn.
        self._cwd_reset_fn: Any | None = None
        # Optional async callback to mark a linked todo as in_progress and
        # store the plan_id back on the todo (bidirectional link).
        # Signature: async fn(todo_id: str, plan_id: str) -> None
        self._todo_start_fn: Any | None = None
        # TeamManager reference — set externally after tool creation so
        # plan(action='delete') can cancel linked teams.
        self._team_manager: Any | None = None
        # Callback to move the orchestrator's working directory into the
        # project folder when a plan sets project_dir.
        # Signature: fn(project_dir_abs: str) -> None
        self._cwd_switch_fn: Any | None = None
        # Sync requirements + tech stack into Cryptex instructions ring.
        # Signature: fn(requirements: str, tech_stack_block: str, tech_stack: dict) -> None
        self._context_sync_fn: Any | None = None
        self._context_clear_fn: Any | None = None

    def set_context_sync_fn(self, fn: Any | None) -> None:
        self._context_sync_fn = fn

    def set_context_clear_fn(self, fn: Any | None) -> None:
        self._context_clear_fn = fn

    def sync_context_from_plan(self, plan: Plan) -> None:
        """Push plan requirements and tech stack into orchestrator context rings."""
        fn = self._context_sync_fn
        if fn is None:
            return
        from nls.agentic.wave_coordination import build_tech_stack_block
        block = build_tech_stack_block(plan=plan)
        try:
            fn(plan.requirements or "", block, dict(plan.tech_stack or {}))
        except Exception:
            logger.debug("Plan context sync failed", exc_info=True)

    def clear_plan_context(self) -> None:
        """Remove plan requirements and tech stack from orchestrator context rings."""
        fn = self._context_clear_fn
        if fn is None:
            return
        try:
            fn()
        except Exception:
            logger.debug("Plan context clear failed", exc_info=True)

    def _project_root(self, plan: Plan) -> Path:
        root = Path(self._workspace)
        if plan.project_dir:
            root = root / plan.project_dir
        return root

    def _align_step_paths(self, plan: Plan) -> list[str]:
        """Validate and normalize step paths relative to plan.project_dir."""
        from nls.agentic.wave_coordination import (
            normalize_plan_step_paths,
            validate_plan_step_paths,
        )
        warnings = validate_plan_step_paths(plan)
        normalize_plan_step_paths(plan)
        return warnings

    def _format_path_warnings(self, warnings: list[str]) -> str:
        if not warnings:
            return ""
        lines = "\n".join(f"  - {w}" for w in warnings[:8])
        extra = (
            f"\n  ... and {len(warnings) - 8} more"
            if len(warnings) > 8
            else ""
        )
        return (
            "\n⚠ STEP PATH MISALIGNMENT — owned_paths/output_files must be "
            "relative to project_dir (e.g. backend/, .gitignore), NOT a "
            f"different folder prefix:\n{lines}{extra}\n"
            "Paths were auto-normalized to project-relative form.\n"
        )

    def _resolve_artifact_path(self, plan: Plan, rel_path: str) -> Path | None:
        """Resolve a step output path under project_dir or workspace."""
        rel = (rel_path or "").strip().replace("\\", "/")
        if not rel:
            return None
        candidates = [self._project_root(plan) / rel, Path(self._workspace) / rel]
        for p in candidates:
            if p.is_file() or p.is_dir():
                return p
        return None

    def _verify_step_artifacts(self, plan: Plan, step: PlanStep) -> tuple[bool, str]:
        """Return (ok, detail) when declared output_files exist on disk."""
        if not step.output_files:
            return False, "step has no output_files to verify"
        found: list[str] = []
        missing: list[str] = []
        for rel in step.output_files:
            if self._resolve_artifact_path(plan, rel) is not None:
                found.append(rel)
            else:
                missing.append(rel)
        if not found:
            return False, (
                f"no artifacts found (expected: {', '.join(step.output_files)})"
            )
        if missing:
            return False, (
                f"partial artifacts — found: {', '.join(found)}; "
                f"missing: {', '.join(missing)}"
            )
        return True, f"verified on disk: {', '.join(found)}"

    def _audit_empty_project_files(self, plan: Plan) -> list[str]:
        """Flag zero-byte or stub-only source files under project_dir."""
        issues: list[str] = []
        root = self._project_root(plan)
        if root is None or not root.is_dir():
            return issues
        skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist"}
        for fpath in root.rglob("*"):
            if not fpath.is_file():
                continue
            if any(p in skip_dirs for p in fpath.parts):
                continue
            if fpath.suffix.lower() not in (
                ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
            ):
                continue
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            if size == 0:
                rel = fpath.relative_to(root)
                issues.append(f"Empty source file (0 bytes): {rel.as_posix()}")
        return issues

    def _audit_delegate_failures(self, plan: Plan) -> list[str]:
        issues: list[str] = []
        if self._team_manager is None:
            return issues
        try:
            teams = self._team_manager.list_teams(include_terminal=True)
        except Exception:
            return issues
        for team in teams:
            if team.plan_id != plan.id:
                continue
            for member in team.members:
                if member.status in ("failed", "cancelled"):
                    _summary = (member.result_summary or "no summary")[:120]
                    issues.append(
                        f"Delegate #{member.delegate_number} failed on step "
                        f"{member.step_id}: {_summary}"
                    )
                elif getattr(self._team_manager, "_delegate_manager", None) is not None:
                    ds = self._team_manager._delegate_manager._delegates.get(
                        member.delegate_number,
                    )
                    if ds and ds.exit_reason == "orchestrator_terminated":
                        issues.append(
                            f"Delegate #{member.delegate_number} was "
                            f"orchestrator-terminated (may be incomplete): "
                            f"{member.task[:80]}"
                        )
        return issues

    def _audit_tech_stack(self, plan: Plan) -> list[str]:
        root = self._project_root(plan)
        if root is None:
            return []
        from nls.agentic.wave_coordination import detect_tech_stack_drift
        return detect_tech_stack_drift(
            plan.requirements,
            str(root),
            tech_stack=plan.tech_stack,
        )

    def _audit_local_tests(self, plan: Plan) -> list[str]:
        """Require evidence of local test execution before completion."""
        incomplete = [s for s in plan.steps if s.status not in ("done", "skipped")]
        if incomplete:
            return []

        issues: list[str] = []
        labels = " ".join(s.label.lower() for s in plan.steps)
        notes = " ".join((s.notes or "").lower() for s in plan.steps)
        combined = labels + " " + notes
        has_test_step = any(
            kw in labels
            for kw in ("local test", "local verify", "run tests", "pytest", "npm test")
        )
        ran_tests = any(
            tok in combined
            for tok in ("pytest", "npm test", "npm run test", "vitest", "jest")
        )
        if not has_test_step:
            issues.append(
                "No dedicated local verification step in plan — add a final "
                "step to run pytest/npm test locally before plan(complete)."
            )
        elif not ran_tests:
            issues.append(
                "Local tests not recorded in step notes — run pytest or "
                "npm test in the project and note results before completing."
            )
        return issues

    def _step_delegate_member(self, plan: Plan, step: PlanStep) -> Any | None:
        tm = self._team_manager
        if tm is None:
            return None
        try:
            teams = tm.list_teams(include_terminal=True)
        except Exception:
            return None
        for team in teams:
            if team.plan_id != plan.id:
                continue
            for member in team.members:
                if member.step_id == step.id:
                    return member
        return None

    def _step_has_failed_delegate(self, plan: Plan, step: PlanStep) -> bool:
        member = self._step_delegate_member(plan, step)
        if member is None:
            return False
        tm = self._team_manager
        if tm is None:
            return member.status in ("failed", "cancelled")
        try:
            for team in tm.list_teams(include_terminal=True):
                if team.plan_id != plan.id:
                    continue
                for m in team.members:
                    if (
                        m.step_id == step.id
                        and m.status in ("failed", "cancelled")
                        and team.status in ("failed", "partial")
                    ):
                        return True
        except Exception:
            pass
        return member.status in ("failed", "cancelled")

    def set_todo_complete_fn(self, fn: Any) -> None:
        """Wire an async callable that marks a todo item done.

        Called automatically when a plan with a linked ``todo_id`` is
        completed, so the Kanban card reflects the finished state without
        requiring the model to issue a separate ``todo(action='complete')``.
        """
        self._todo_complete_fn = fn

    def set_cwd_reset_fn(self, fn: Any) -> None:
        """Wire a callable that resets the orchestrator CWD to workspace root.

        Called on plan completion so that subsequent writes (e.g. research
        notes, reports) are not accidentally placed inside the completed
        project folder.  Same signature as ``_cwd_switch_fn``: takes the
        absolute workspace root path as its only argument.
        """
        self._cwd_reset_fn = fn

    def set_todo_start_fn(self, fn: Any) -> None:
        """Wire an async callable that marks a todo item in_progress and
        stores the plan_id on it when a plan is created with ``todo_id``.
        """
        self._todo_start_fn = fn

    def set_cwd_switch_fn(self, fn: Any) -> None:
        """Wire a callable that shifts the orchestrator's CWD into the
        project directory.  Called when ``plan(action='create')`` or
        ``plan(action='update')`` sets/changes ``project_dir``.

        Signature: ``fn(project_dir_abs: str) -> None``
        """
        self._cwd_switch_fn = fn

    def set_inference_fn(self, fn: Any) -> None:
        """Set an async callable ``fn(prompt) -> str`` for semantic
        verification of acceptance criteria during plan verify."""
        self._inference_fn = fn

    def set_dep_inference_fn(self, fn: Any) -> None:
        """Set a dedicated inference callable for dependency graph fixing.

        Falls back to ``_inference_fn`` if not set.
        """
        self._dep_inference_fn = fn

    @property
    def name(self) -> str:
        return "plan"

    @property
    def description(self) -> str:
        return (
            "Execution plan for a todo — this is HOW you do a task. "
            "Always link to a todo via todo_id when creating a plan. "
            "The plan auto-sets the linked todo to in_progress on create "
            "and auto-completes it on plan completion. "
            "Actions: 'create' (new plan — pass todo_id, requirements, tech_stack, steps), "
            "'read' (current plan), "
            "'set_requirements' (update PRD text — refreshes context ring), "
            "'set_tech_stack' (lock stack — pass tech_stack={...}), "
            "'update' (mark step done/in-progress; pass owned_paths to scope delegates), "
            "'add_step' (add a step to an existing plan — pass label, delegatable), "
            "'sub_plan' (linked JSON sub-plan for a complex step), "
            "'verify' (audit output against acceptance criteria — checks empty "
            "files, stack drift, delegate failures, local test evidence), "
            "'complete' (mark plan done only when every step is done, "
            "verify passed, and no partial wave — auto-completes linked todo). "
            "Include a final local verification step (pytest/npm test) in every "
            "plan before complete. "
            "'delegate' (mark a step as delegated to a sub-agent), "
            "'accept_partial' (mark step done after failed/cancelled wave OR "
            "when output_files exist on disk but no delegate ran the step), "
            "'continue_work' (import pending/failed steps from another root "
            "plan into the active plan, then archive the source — use instead "
            "of plan(create) for remainder work), "
            "'fix_dependencies' (run dependency inference + break service↔API "
            "cycles — use when team(launch) blocks or steps 4↔5 cycle), "
            "'delete' (last resort — archives plan; prefer fix_dependencies "
            "or continue_work when work is already done; pass reason='...'). "
            "Plans persist as JSON files.\n\n"
            "PLAN POSITION: You periodically receive a sliding "
            "window showing the previous step (with outcome), the current "
            "step, and the next step coming up. "
            "Use this for orientation — work on the current step at a "
            "natural pace, use the previous step's outcome for context, "
            "and anticipate the next step.\n\n"
            "IMPORTANT: When marking a step as 'done', you MUST include "
            "'notes' describing the concrete evidence of completion "
            "(e.g. 'snapshot shows dashboard loaded', 'build output: 0 errors', "
            "'file has 120 lines of working code'). "
            "Do NOT mark a step done in the same response as the action — "
            "wait for the result, verify it succeeded, then mark done "
            "in a SEPARATE response.\n\n"
            "CONTEXT RECORDING: Each step has a 'description' field "
            "(set via steps array during create, or 'step_description' in "
            "update/add_step). Use it to record prep work, credentials, URLs, "
            "decisions from conversation, and existing files. "
            "Team members see this context when they pick up the step. "
            "If you did prep work before delegating (created folders, wrote "
            "starters, gathered info), update the step description so the "
            "delegate knows what exists and what's expected."
            + tool_description_supplement("plan")
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create", "read", "update", "add_step",
                        "set_requirements", "set_tech_stack",
                        "sub_plan", "verify", "complete",
                        "delegate", "delete", "accept_partial", "continue_work",
                        "fix_dependencies",
                    ],
                    "description": "The action to perform.",
                },
                "title": {
                    "type": "string",
                    "description": "Plan title (required for create/sub_plan). Pass '-' for other actions.",
                },
                "requirements": {
                    "type": "string",
                    "description": (
                        "Full task/PRD requirements text. Required on create. "
                        "Use set_requirements to update later."
                    ),
                },
                "tech_stack": {
                    "type": "object",
                    "description": (
                        "Structured mandatory stack — set on create or via "
                        "set_tech_stack. Keys: backend_language, backend_framework, "
                        "frontend_framework, database, orm, package_manager, "
                        "deploy_target (plus custom keys as needed)."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of concrete acceptance criteria (for create/sub_plan).",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {
                                "type": "string",
                                "description": (
                                    "Detailed context for this step — include "
                                    "prep work done, credentials, URLs, decisions "
                                    "from conversation. Team members see this."
                                ),
                            },
                            "output_files": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Expected artifacts relative to project_dir "
                                    "(e.g. 'backend/', '.gitignore') — not "
                                    "project_dir/filename."
                                ),
                            },
                            "owned_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Path patterns relative to project_dir "
                                    "(e.g. 'backend/', 'frontend/') — do NOT "
                                    "prefix with project_dir name."
                                ),
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "IDs of steps this step depends on.",
                            },
                            "delegatable": {
                                "type": "boolean",
                                "description": "Whether this step can be delegated to a sub-agent.",
                            },
                        },
                    },
                    "description": "Plan steps (for create/sub_plan).",
                },
                "files": {
                    "type": "object",
                    "description": (
                        "File scaffolding map: {filename: {purpose, status}} "
                        "(for create/sub_plan)."
                    ),
                },
                "step_id": {
                    "type": "string",
                    "description": (
                        "REQUIRED for 'update' action. The step ID to update "
                        "(e.g. 'step-1', 'step-2'). Get step IDs from "
                        "plan(action='read') or the create response."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "skipped", "failed"],
                    "description": "New status (for update action).",
                },
                "notes": {
                    "type": "string",
                    "description": "Completion evidence notes (for update action).",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why partial completion is acceptable "
                        "(required for accept_partial)."
                    ),
                },
                "step_description": {
                    "type": "string",
                    "description": (
                        "Detailed context for a step — prep work done, "
                        "credentials, URLs, decisions. Used by update and "
                        "add_step. Team members see this when they receive "
                        "their task."
                    ),
                },
                "project_dir": {
                    "type": "string",
                    "description": (
                        "Project directory name. For create: a short descriptive slug "
                        "(e.g. 'icf-coaching-evaluation'). Auto-generated from title if omitted. "
                        "For update: changes the plan's project directory."
                    ),
                },
                "parent_step_id": {
                    "type": "string",
                    "description": "Parent step ID to attach sub-plan to (for sub_plan).",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Specific plan ID to operate on (optional, defaults to active plan).",
                },
                "todo_id": {
                    "type": "string",
                    "description": "Linked todo item ID (for create).",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Deprecated for complete — ignored. All steps must be "
                        "status=done with verification before complete."
                    ),
                },
                "force_new": {
                    "type": "boolean",
                    "description": (
                        "For 'create' only: bypass the one-plan-per-project "
                        "guard when the user explicitly asks for a SEPARATE "
                        "project. Do NOT use this to split one project into "
                        "multiple plans."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": "Step label/description (for add_step).",
                },
                "delegatable": {
                    "type": "boolean",
                    "description": "Whether this step can be delegated to a sub-agent (for add_step). Default: false.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Step IDs or labels this step depends on (for add_step "
                        "or update)."
                    ),
                },
                "force_delete": {
                    "type": "boolean",
                    "description": (
                        "For delete only: bypass guard when plan has done steps "
                        "and pending remainder (user explicitly abandoning plan)."
                    ),
                },
                "output_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Expected artifacts relative to project_dir "
                        "(e.g. 'backend/', 'README.md')."
                    ),
                },
                "owned_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Delegate scope relative to project_dir (add_step/update)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why the plan is being deleted (for delete). Logged for audit.",
                },
            },
            "required": ["action", "title"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        dispatch = {
            "create": self._create,
            "read": self._read,
            "update": self._update,
            "set_requirements": self._set_requirements,
            "set_tech_stack": self._set_tech_stack,
            "add_step": self._add_step,
            "sub_plan": self._sub_plan,
            "verify": self._verify,
            "complete": self._complete,
            "delegate": self._delegate,
            "delete": self._delete,
            "accept_partial": self._accept_partial,
            "continue_work": self._continue_work,
            "fix_dependencies": self._fix_dependencies,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(
                content=f"Unknown plan action: '{action}'. "
                f"Use one of: {', '.join(dispatch.keys())}",
                is_error=True,
            )
        return await handler(params)

    def _blocked_delegatable_done_without_delegate(
        self, plan: Plan, step: PlanStep, *, notes: str = "",
    ) -> str | None:
        """Block marking a delegatable step done without delegate completion."""
        if not step.delegatable:
            return None
        if step.notes and (
            "[accept_partial]" in step.notes
            or "[verified_on_disk]" in step.notes
        ):
            return None
        member = self._step_delegate_member(plan, step)
        if member is not None and member.status == "done":
            return None

        verified, detail = self._verify_step_artifacts(plan, step)
        if verified:
            if (notes or "").strip():
                return None
            return (
                f"BLOCKED: Step '{step.id}' artifacts exist ({detail}) but "
                "notes are required.\n"
                f"Use plan(action='update', step_id='{step.id}', status='done', "
                f"notes='{detail}') or plan(action='accept_partial', "
                f"step_id='{step.id}', reason='...', notes='...')."
            )

        return (
            f"BLOCKED: Cannot mark delegatable step '{step.id}' done — "
            "no team member completed it.\n"
            "Launch a wave for this step, or if output_files already exist "
            "(e.g. built by an earlier step's delegate), verify with read/list_dir "
            f"then plan(action='accept_partial', step_id='{step.id}', "
            "reason='...', notes='verified: path/to/file').\n"
            "After a failed wave use accept_partial with artifact notes.\n"
            "For a large retry scope use plan(action='sub_plan', "
            f"parent_step_id='{step.id}', title='...', steps=[...])."
        )

    def _blocked_done_without_accept(
        self, plan: Plan, step: PlanStep,
    ) -> str | None:
        """Block naked plan(update→done) when linked delegate failed/cancelled."""
        tm = self._team_manager
        if tm is None:
            return None
        try:
            teams = tm.list_teams(include_terminal=True)
        except Exception:
            return None
        for team in teams:
            if team.plan_id != plan.id:
                continue
            for member in team.members:
                if member.step_id != step.id:
                    continue
                if member.status in ("failed", "cancelled") and team.status in (
                    "failed", "partial",
                ):
                    return (
                        f"BLOCKED: Cannot mark '{step.id}' done — delegate "
                        f"#{member.delegate_number} is {member.status} "
                        f"(team {team.id}, wave outcome={team.status}).\n"
                        "Verify artifacts on disk in switch_mode(evaluating), "
                        "then use plan(action='accept_partial', "
                        f"step_id='{step.id}', notes='...', reason='...')."
                    )
        return None

    # -- Actions -------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> ToolResult:
        title = params.get("title", "").strip()
        if not title:
            return ToolResult(
                content="Error: 'title' is required for create.",
                is_error=True,
            )

        existing = self._store.find_active()
        _force_new = params.get("force_new", False)
        if existing and not _force_new:
            step_lines = "\n".join(
                f"  - {s.id}: {s.label} ({s.status})" for s in existing.steps
            )
            _exist_dir = ""
            if existing.project_dir:
                _exist_dir = f"Project directory: {existing.project_dir}/\n"
            return ToolResult(
                content=(
                    f"BLOCKED: A root plan already exists — you must use it.\n"
                    f"Plan: {existing.id}\n"
                    f"Title: {existing.title}\n"
                    + _exist_dir
                    + f"Status: {existing.status}\n"
                    f"Steps ({existing.progress_summary()}):\n{step_lines}\n\n"
                    f"ONE PROJECT = ONE PLAN. Do NOT create separate plans for "
                    f"backend/frontend/deployment — add them as steps in this "
                    f"plan using plan(action='add_step', plan_id='{existing.id}', "
                    f"label='...', delegatable=true, depends_on=[...]).\n"
                    f"Or use plan(action='sub_plan', parent_step_id='step-N', ...) "
                    f"to retry a failed step.\n"
                    f"Or plan(action='continue_work', source_plan_id='{existing.id}') "
                    f"to import remaining steps into the active plan (archives source).\n"
                    f"If this plan is stale or wrong, delete/archive it first with "
                    f"plan(action='delete', plan_id='{existing.id}', reason='...').\n"
                    f"If the user asked for a completely SEPARATE project, "
                    f"pass force_new=true (archives other active root plans)."
                ),
                is_error=True,
                details={
                    "plan_id": existing.id,
                    "action": "create",
                    "already_existed": True,
                    "steps": [
                        {"id": s.id, "label": s.label} for s in existing.steps
                    ],
                },
            )

        # Reuse existing project folder from ANY prior root plan (even
        # failed/completed ones) to prevent duplicate workspace folders.
        _reuse_dir = ""
        if not params.get("project_dir"):
            if existing and existing.project_dir:
                _reuse_dir = existing.project_dir
            else:
                _reuse_dir = self._store.find_any_project_dir()

        plan = self._store.create_plan(
            title=title,
            requirements=params.get("requirements", ""),
            tech_stack=self._parse_tech_stack(params.get("tech_stack")),
            acceptance_criteria=params.get("acceptance_criteria"),
            steps=params.get("steps"),
            scaffolding=params.get("files"),
            todo_id=params.get("todo_id"),
            project_dir=params.get("project_dir") or _reuse_dir,
        )

        _archive_reason = (
            params.get("reason", "").strip()
            or f"superseded by {plan.id}"
        )
        _archived = self._store.archive_sibling_active_roots(
            plan.id, reason=_archive_reason,
        )

        # Micro-inference: validate and fix dependency graph.
        _dep_warning = await self._infer_dependencies(plan)

        _path_warnings = self._align_step_paths(plan)

        plan.status = "in_progress"
        self._store.save(plan)
        self.sync_context_from_plan(plan)

        _path_note = self._format_path_warnings(_path_warnings)

        _stack_warning = ""
        if not plan.tech_stack:
            _stack_warning = (
                "\n⚠ TECH STACK MISSING: Pass tech_stack on create or call "
                f"plan(action='set_tech_stack', tech_stack={{...}}) NOW "
                "before delegating — delegates use this to avoid stack drift.\n"
            )
        if not (plan.requirements or "").strip():
            _stack_warning += (
                "\n⚠ REQUIREMENTS MISSING: Pass requirements on create or call "
                "plan(action='set_requirements', requirements='...').\n"
            )

        # Shift the orchestrator's CWD into the project folder so that
        # bash / write / edit all resolve relative paths inside the
        # project, preventing stray files at the workspace root.
        if plan.project_dir and self._cwd_switch_fn:
            from pathlib import Path as _Path
            _pd_abs = str(_Path(self._workspace) / plan.project_dir)
            try:
                self._cwd_switch_fn(_pd_abs)
                logger.info(
                    "Orchestrator CWD shifted to project dir: %s",
                    _pd_abs,
                )
            except Exception as _e:
                logger.warning("CWD switch failed: %s", _e)

        # Auto-sync: set the linked todo to in_progress and store plan_id.
        _todo_note = ""
        if plan.todo_id and self._todo_start_fn is not None:
            try:
                await self._todo_start_fn(plan.todo_id, plan.id)
                _todo_note = f"\nLinked todo {plan.todo_id} set to in_progress."
            except Exception as _te:
                logger.debug("Auto-start todo %s failed: %s", plan.todo_id, _te)

        step_lines = "\n".join(
            f"  - {s.id}: {s.label}" for s in plan.steps
        )
        _dir_note = ""
        if plan.project_dir:
            _dir_note = (
                f"⚠ PROJECT DIRECTORY: {plan.project_dir}/\n"
                f"USE THIS DIRECTORY for all project files. Do NOT create "
                f"a different folder — this one was auto-created for you.\n"
                f"If you prefer a different name, update it with: "
                f"plan(action='update', step_id='step-1', "
                f"project_dir='{plan.project_dir}')\n"
            )

        _dep_note = ""
        if _dep_warning:
            from nls.agentic.plan_store import get_delegation_waves
            _waves = get_delegation_waves(plan)
            _wave_sizes = [len(w) for w in _waves]
            _dep_note = (
                f"\n⚠ DEPENDENCY GRAPH WARNING — automatic fix failed:\n"
                f"{_dep_warning}\n"
                f"Current waves ({len(_waves)}): "
                f"{' → '.join(str(s) for s in _wave_sizes)}\n"
                f"Review your step dependencies NOW. Steps that need code "
                f"from other steps MUST list those as depends_on. Use "
                f"plan(action='fix_dependencies') or plan(action='update', "
                f"step_id='...', depends_on=[...]). Do NOT delete the plan.\n"
            )

        # Shallow plan detection: warn if the plan looks like just
        # one phase of a larger project rather than a full master plan.
        _shallow_warning = ""
        _step_labels_lower = [s.label.lower() for s in plan.steps]
        _all_setup = all(
            any(kw in lbl for kw in (
                "init", "setup", "scaffold", "create repo", "clone",
                "install", "config", "environment", "package.json",
                "directory", "structure", "entry point",
            ))
            for lbl in _step_labels_lower
        )
        _has_few_steps = len(plan.steps) <= 6
        if _has_few_steps and _all_setup:
            _shallow_warning = (
                "\n\n⚠ SHALLOW PLAN WARNING: This plan appears to cover "
                "ONLY project setup/scaffolding, not the full project.\n"
                "A master plan for a full-stack application should include "
                "steps for the ENTIRE lifecycle:\n"
                "  • Scaffolding / project init (Wave 0)\n"
                "  • Database schema / models\n"
                "  • Backend API / services\n"
                "  • Frontend / UI components\n"
                "  • External integrations (APIs, AI services)\n"
                "  • Authentication / authorization\n"
                "  • Deployment / infrastructure\n"
                "  • Testing / QA\n"
                "Add the missing steps NOW with "
                f"plan(action='add_step', plan_id='{plan.id}', "
                "label='...', delegatable=true, depends_on=[...]) "
                "BEFORE creating a team."
            )
        elif len(plan.steps) <= 5 and plan.project_dir:
            _shallow_warning = (
                f"\n\n⚠ INSUFFICIENT PLAN: Only {len(plan.steps)} steps for "
                "a full project. This is TOO FEW — each mega-step will be "
                "too large for a single delegate to complete well.\n"
                "REQUIRED: Break this into 7-12 focused steps. Each step "
                "should be a single, completable unit of work:\n"
                "  • Repository / project init\n"
                "  • Database schema + models\n"
                "  • Backend API endpoints\n"
                "  • Backend service integrations (APIs, AI, etc.)\n"
                "  • Frontend scaffolding + routing\n"
                "  • Frontend core UI components\n"
                "  • Authentication / authorization\n"
                "  • Integration wiring (frontend ↔ backend)\n"
                "  • Deployment configuration\n"
                "  • Testing / QA\n"
                "Add the missing steps NOW with "
                f"plan(action='add_step', plan_id='{plan.id}', "
                "label='...', delegatable=true, depends_on=[...]) "
                "BEFORE creating a team."
            )
        elif _has_few_steps and plan.project_dir:
            _shallow_warning = (
                f"\n\n⚠ NOTE: Only {len(plan.steps)} steps. For a full "
                "project, ensure ALL phases are covered (backend, frontend, "
                "integrations, deployment). Add missing steps with "
                f"plan(action='add_step', plan_id='{plan.id}')."
            )

        _archived_note = ""
        if _archived:
            _archived_note = (
                f"\nArchived other active root plan(s): {', '.join(_archived)} "
                f"(one project = one active plan).\n"
            )

        return ToolResult(
            content=(
                f"Plan created: {plan.id}\n"
                f"Title: {plan.title}\n"
                + _dir_note
                + _archived_note
                + f"Steps ({len(plan.steps)}):\n{step_lines}\n"
                f"Criteria: {len(plan.acceptance_criteria)}\n"
                f"Saved to: .plans/{plan.id}.json\n\n"
                f"Use the step IDs above when updating steps.\n"
                f"Read the plan before each step to stay grounded."
                + _todo_note
                + _dep_note
                + _stack_warning
                + _path_note
                + _shallow_warning
            ),
            details={
                "plan_id": plan.id,
                "action": "create",
                "todo_id": plan.todo_id,
                "project_dir": plan.project_dir,
                "steps": [
                    {"id": s.id, "label": s.label, "delegatable": s.delegatable}
                    for s in plan.steps
                ],
            },
        )

    def _diagnose_dependency_graph(self, plan: Plan) -> str:
        """Structural heuristic analysis of the dependency graph.

        Returns a diagnostic string describing any detected anti-patterns.
        This is passed to micro-inference so the LLM knows what to fix.
        """
        from nls.agentic.plan_store import get_delegation_waves

        waves = get_delegation_waves(plan)
        n_steps = len(plan.steps)
        diagnostics: list[str] = []

        # --- Flat graph: one wave holds most of the work ---
        if len(waves) <= 2 and n_steps >= 4:
            biggest = max(len(w) for w in waves)
            if biggest >= n_steps - 1:
                diagnostics.append(
                    f"FLAT GRAPH: {biggest}/{n_steps} steps land in a "
                    f"single wave. This means almost everything runs in "
                    f"parallel with no sequencing."
                )

        # --- Shallow depth: everything points to the same root ---
        dep_targets: dict[str, int] = {}
        for step in plan.steps:
            for d in step.depends_on:
                dep_targets[d] = dep_targets.get(d, 0) + 1
        if dep_targets:
            top_target, top_count = max(
                dep_targets.items(), key=lambda kv: kv[1],
            )
            if top_count >= n_steps - 1 and len(dep_targets) == 1:
                diagnostics.append(
                    f"SINGLE-ROOT: all {top_count} dependent steps point "
                    f"only to \"{top_target}\". No deeper sequencing exists."
                )

        # --- Deployment/release step without deep dependencies ---
        _deploy_keywords = {"deploy", "release", "publish", "ship", "railway"}
        for i, step in enumerate(plan.steps):
            label_lower = step.label.lower()
            if any(kw in label_lower for kw in _deploy_keywords):
                dep_indices = set()
                for d in step.depends_on:
                    for j, s in enumerate(plan.steps):
                        if s.label == d or s.id == d:
                            dep_indices.add(j)
                if dep_indices and max(dep_indices) < n_steps - 2:
                    diagnostics.append(
                        f"EARLY DEPLOY: step {i+1} (\"{step.label}\") "
                        f"depends only on early steps — it should depend "
                        f"on the implementation steps it will deploy."
                    )

        # --- Integration steps parallel with their prerequisites ---
        _integration_kw = {"interactive", "integration", "e2e", "end-to-end"}
        _frontend_kw = {"frontend", "react", "ui", "vue", "angular"}
        _backend_kw = {"backend", "api", "endpoint", "fastapi", "server"}
        for step in plan.steps:
            ll = step.label.lower()
            if any(kw in ll for kw in _integration_kw):
                dep_labels = {d.lower() for d in step.depends_on}
                has_fe = any(
                    any(kw in dl for kw in _frontend_kw)
                    for dl in dep_labels
                )
                has_be = any(
                    any(kw in dl for kw in _backend_kw)
                    for dl in dep_labels
                )
                if not has_fe and not has_be:
                    diagnostics.append(
                        f"INTEGRATION WITHOUT DEPS: \"{step.label}\" looks "
                        f"like an integration step but doesn't depend on "
                        f"any frontend or backend steps."
                    )

        # --- Wave size distribution ---
        if len(waves) >= 2:
            sizes = [len(w) for w in waves]
            diagnostics.append(
                f"Wave sizes: {' → '.join(str(s) for s in sizes)} "
                f"({len(waves)} waves total)"
            )

        return "\n".join(diagnostics) if diagnostics else ""

    async def _infer_dependencies(self, plan: Plan) -> str:
        """Always-on dependency validation via micro-inference.

        Runs a structural heuristic first to diagnose anti-patterns, then
        passes those findings to a fast LLM call that validates and fixes
        the dependency graph.  The heuristic is diagnostic, not a gate —
        the LLM always gets the final say.

        Returns a diagnostic string (empty if graph is healthy).  The
        caller can surface this in the tool result so the orchestrator
        knows if the graph was fixed or needs manual attention.
        """
        _infer_fn = self._dep_inference_fn or self._inference_fn
        if _infer_fn is None:
            return ""

        delegatable = [s for s in plan.steps if s.delegatable]
        if len(delegatable) < 2:
            return ""

        diagnosis = self._diagnose_dependency_graph(plan)

        step_list = "\n".join(
            f'{i + 1}. "{s.label}" (current deps: '
            f'{[d for d in s.depends_on] if s.depends_on else "none"})'
            for i, s in enumerate(plan.steps)
        )

        diagnosis_block = ""
        if diagnosis:
            diagnosis_block = (
                f"\n⚠ Structural analysis found issues:\n{diagnosis}\n"
            )

        prompt = (
            "/no_think\n"
            "Validate and fix the dependency graph for this project plan.\n\n"
            f"Steps (with their current dependencies):\n{step_list}\n"
            f"{diagnosis_block}\n"
            "Rules:\n"
            "- Model the real data flow: if step B needs code/files that "
            "step A creates, B MUST depend on A.\n"
            "- Scaffolding/init is wave 0. Core infrastructure (DB, backend "
            "framework, frontend framework) is wave 1. Services that need "
            "that infrastructure are wave 2+. Integration/testing is later. "
            "Deployment/release is ALWAYS the final wave.\n"
            "- Steps that are truly independent CAN run in parallel within "
            "a wave — but only if neither needs the other's output.\n"
            "- Do NOT make everything depend only on scaffolding — that "
            "creates a flat graph where 8+ agents collide.\n\n"
            "Return a JSON object mapping each step number to the list of "
            "step numbers it MUST wait for (direct dependencies only).\n"
            "Reply with ONLY the JSON object, no explanation.\n"
            'Example: {"1": [], "2": [1], "3": [1], "4": [2, 3], '
            '"5": [2, 3], "6": [4, 5], "7": [1], "8": [6, 7], "9": [6], '
            '"10": [8, 9]}'
        )

        _MAX_ATTEMPTS = 2
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw = await _infer_fn(prompt)
                raw = raw.strip()
                # Strip <think>...</think> blocks (model may emit reasoning)
                if "<think>" in raw and "</think>" in raw:
                    _after_think = raw.split("</think>", 1)[-1].strip()
                    if _after_think:
                        raw = _after_think
                    else:
                        _inside = raw.split("<think>", 1)[-1].split("</think>", 1)[0]
                        _brace = _inside.rfind("{")
                        if _brace >= 0:
                            raw = _inside[_brace:]
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                _json_start = raw.find("{")
                if _json_start > 0:
                    raw = raw[_json_start:]
                dep_map = json.loads(raw)
                if not isinstance(dep_map, dict):
                    raise ValueError(f"Expected dict, got {type(dep_map).__name__}")

                label_by_idx = {i + 1: s.label for i, s in enumerate(plan.steps)}
                patched = 0
                for step_key, deps in dep_map.items():
                    idx = int(step_key)
                    if idx < 1 or idx > len(plan.steps):
                        continue
                    step = plan.steps[idx - 1]
                    if not isinstance(deps, list):
                        continue
                    resolved = []
                    for d in deps:
                        d_int = int(d)
                        if d_int in label_by_idx:
                            resolved.append(label_by_idx[d_int])
                    old_deps = set(step.depends_on)
                    new_deps = set(resolved)
                    if new_deps != old_deps:
                        step.depends_on = resolved
                        patched += 1

                if patched:
                    from nls.agentic.plan_store import get_delegation_waves
                    new_waves = get_delegation_waves(plan)
                    wave_sizes = [len(w) for w in new_waves]
                    logger.info(
                        "PlanTool: dependency inference patched %d/%d steps "
                        "→ %d waves (%s) [attempt %d]%s",
                        patched, len(plan.steps), len(new_waves),
                        " → ".join(str(s) for s in wave_sizes),
                        attempt,
                        f" | diagnostics: {diagnosis}" if diagnosis else "",
                    )
                else:
                    logger.info(
                        "PlanTool: dependency inference validated graph "
                        "(no changes needed)%s",
                        f" | diagnostics: {diagnosis}" if diagnosis else "",
                    )

                # Re-apply store safety net if inference flattened the graph.
                _safety = self._store.ensure_dependency_safety_net(plan)
                if _safety:
                    from nls.agentic.plan_store import get_delegation_waves
                    _nw = get_delegation_waves(plan)
                    logger.info(
                        "PlanTool: dependency safety net re-patched %d step(s) "
                        "after inference → %d waves (%s)",
                        _safety,
                        len(_nw),
                        " → ".join(str(len(w)) for w in _nw),
                    )
                self._store.save(plan)
                return ""  # success — no warning to surface

            except Exception as exc:
                logger.warning(
                    "PlanTool: dependency inference FAILED (attempt %d/%d): %s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    # Retry with a much simpler prompt
                    prompt = (
                        "/no_think\n"
                        f"There are {len(plan.steps)} project steps:\n{step_list}\n\n"
                        "Output a JSON object mapping step number → list of step "
                        "numbers it depends on. Scaffolding has no deps. "
                        "Deployment depends on everything. "
                        "ONLY output the JSON, nothing else.\n"
                        'Example: {"1":[],"2":[1],"3":[1,2],"4":[3]}'
                    )

        # Both attempts failed — return diagnostic for the caller to surface
        if diagnosis:
            logger.warning(
                "PlanTool: dependency inference exhausted %d attempts. "
                "Diagnosis: %s",
                _MAX_ATTEMPTS, diagnosis,
            )
            return diagnosis
        return ""

    async def _read(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(
                content="No active plan found. Create one with plan(action='create').",
                is_error=True,
            )
        return ToolResult(
            content=plan.to_context_string(),
            details={"plan_id": plan.id, "action": "read"},
        )

    def _parse_tech_stack(self, raw: Any) -> dict[str, str]:
        from nls.agentic.wave_coordination import normalize_tech_stack_param
        return normalize_tech_stack_param(raw)

    async def _set_requirements(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)
        req = (params.get("requirements") or "").strip()
        if not req:
            return ToolResult(
                content="Error: 'requirements' text is required for set_requirements.",
                is_error=True,
            )
        plan.requirements = req
        plan.touch()
        self._store.save(plan)
        self.sync_context_from_plan(plan)
        return ToolResult(
            content=(
                f"Requirements updated for plan {plan.id} "
                f"({len(req)} chars). Tech stack ring refreshed."
            ),
            details={"plan_id": plan.id, "action": "set_requirements"},
        )

    async def _set_tech_stack(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)
        stack = self._parse_tech_stack(params.get("tech_stack"))
        if not stack:
            return ToolResult(
                content=(
                    "Error: 'tech_stack' object is required for set_tech_stack. "
                    "Example: tech_stack={backend_language:'typescript', "
                    "backend_framework:'express', frontend_framework:'react'}"
                ),
                is_error=True,
            )
        plan.tech_stack = stack
        plan.touch()
        self._store.save(plan)
        self.sync_context_from_plan(plan)
        from nls.agentic.wave_coordination import format_structured_tech_stack
        summary = format_structured_tech_stack(stack)
        return ToolResult(
            content=(
                f"Tech stack locked for plan {plan.id}:\n{summary}\n\n"
                "Delegates and verify audits will use this stack."
            ),
            details={"plan_id": plan.id, "action": "set_tech_stack", "tech_stack": stack},
        )

    async def _update(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        step_id = params.get("step_id", "")
        if not step_id:
            step_ids = [f"{s.id}: {s.label}" for s in plan.steps[:5]]
            return ToolResult(
                content=(
                    "Error: 'step_id' is required for update. "
                    f"Available steps:\n" + "\n".join(f"  - {s}" for s in step_ids)
                    + "\nPass step_id='step-N' to update a specific step."
                ),
                is_error=True,
            )
        step = plan.get_step(step_id)
        if step is None:
            step = self._fuzzy_match_step(plan, step_id)
        if step is None:
            valid = ", ".join(
                f"{s.id} ({s.label})" for s in plan.steps
            )
            return ToolResult(
                content=f"No step '{step_id}' in plan. Valid: {valid}",
                is_error=True,
            )

        resolved_id = step.id

        new_status = params.get("status")
        new_notes = params.get("notes")

        if new_status == "done" and not new_notes:
            return ToolResult(
                content=(
                    f"Cannot mark step '{resolved_id}' as done without evidence. "
                    f"Include 'notes' describing what concrete result proves "
                    f"this step succeeded (e.g. 'page shows confirmation', "
                    f"'build passed with 0 errors'). "
                    f"If you haven't verified the result on disk yet, do that first."
                ),
                is_error=True,
            )

        if new_status == "done":
            _dlg_block = self._blocked_delegatable_done_without_delegate(
                plan, step, notes=(new_notes or ""),
            )
            if _dlg_block:
                return ToolResult(content=_dlg_block, is_error=True)
            _done_block = self._blocked_done_without_accept(plan, step)
            if _done_block:
                return ToolResult(content=_done_block, is_error=True)
            if (
                step.delegatable
                and self._step_delegate_member(plan, step) is None
                and (new_notes or "").strip()
            ):
                _verified, _vdetail = self._verify_step_artifacts(plan, step)
                if _verified and "[verified_on_disk]" not in (new_notes or ""):
                    new_notes = f"[verified_on_disk] {_vdetail}\n{new_notes}"

        # Guard: check for required tool calls referenced in the plan
        # context that were never invoked during this agentic session.
        if new_status == "done" and self._invoked_tools is not None:
            missing = self._check_required_tools(plan, step)
            if missing:
                tool_list = ", ".join(missing)
                return ToolResult(
                    content=(
                        f"Cannot mark step '{resolved_id}' as done — "
                        f"the plan instructions reference tool(s) you "
                        f"haven't called yet: {tool_list}. "
                        f"Please call them first, then mark this step done."
                    ),
                    is_error=True,
                )

        _status_skip_note = ""
        if new_status == "in_progress" and step.status != "in_progress":
            unsatisfied = self._unsatisfied_dependencies(plan, step)
            if unsatisfied:
                _status_skip_note = (
                    f"\nNote: status kept as {step.status} — cannot set "
                    f"in_progress until dependencies are done:\n"
                    + "\n".join(f"  - {u}" for u in unsatisfied)
                )
                new_status = None

        new_desc = params.get("step_description")
        new_project_dir = (params.get("project_dir") or "").strip()
        new_depends_on = params.get("depends_on")
        new_owned_paths = params.get("owned_paths")
        new_output_files = params.get("output_files")

        if new_depends_on is not None:
            if not isinstance(new_depends_on, list):
                return ToolResult(
                    content="depends_on must be an array of step labels or IDs.",
                    is_error=True,
                )
            step.depends_on = [str(d).strip() for d in new_depends_on if str(d).strip()]

        if new_project_dir and new_project_dir != plan.project_dir:
            from pathlib import Path as _Path
            _pd_abs = str(_Path(self._workspace) / new_project_dir)
            _Path(_pd_abs).mkdir(parents=True, exist_ok=True)
            plan.project_dir = new_project_dir
            if self._cwd_switch_fn:
                try:
                    self._cwd_switch_fn(_pd_abs)
                except Exception:
                    pass

        if new_status:
            step.status = new_status
        if new_notes:
            step.notes = new_notes
        if new_desc:
            step.description = new_desc
        if new_owned_paths is not None:
            if not isinstance(new_owned_paths, list):
                return ToolResult(
                    content="owned_paths must be an array of path patterns.",
                    is_error=True,
                )
            step.owned_paths = [str(p).strip() for p in new_owned_paths if str(p).strip()]

        if new_output_files is not None:
            if not isinstance(new_output_files, list):
                return ToolResult(
                    content="output_files must be an array of path patterns.",
                    is_error=True,
                )
            step.output_files = [
                str(p).strip() for p in new_output_files if str(p).strip()
            ]

        _path_note = ""
        if (
            new_owned_paths is not None
            or new_output_files is not None
            or new_project_dir
        ):
            _path_warnings = self._align_step_paths(plan)
            _path_note = self._format_path_warnings(_path_warnings)
        if new_status == "done" and step.output_files:
            for fname in step.output_files:
                if fname in plan.scaffolding:
                    plan.scaffolding[fname]["status"] = "created"

        self._store.save(plan)

        if (
            self._team_manager is not None
            and (new_owned_paths is not None or new_output_files is not None)
        ):
            try:
                self._team_manager.sync_step_owned_paths_to_wave(
                    plan.id, step.id,
                )
            except Exception:
                pass

        _dep_note = ""
        if new_depends_on is not None:
            _dep_note = (
                f"\nDepends on: {', '.join(step.depends_on) or '(none)'}"
            )
            from nls.agentic.plan_store import format_dependency_cycle_hints
            _cycle_hint = format_dependency_cycle_hints(plan)
            if _cycle_hint:
                _dep_note += f"\n\n⚠ {_cycle_hint}"

        return ToolResult(
            content=(
                f"Updated step '{resolved_id}': {step.label}\n"
                f"Status: {step.status}"
                + _dep_note
                + _path_note
                + _status_skip_note
                + f"\nPlan progress: {plan.progress_summary()}"
            ),
            details={
                "plan_id": plan.id,
                "step_id": resolved_id,
                "action": "update",
                "status": step.status,
            },
        )

    async def _accept_partial(self, params: dict[str, Any]) -> ToolResult:
        """Mark a step done after failed wave or verified on-disk artifacts."""
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        step_id = (params.get("step_id") or "").strip()
        notes = (params.get("notes") or "").strip()
        reason = (params.get("reason") or "").strip()
        if not step_id:
            return ToolResult(
                content=(
                    "Error: 'step_id' is required for accept_partial.\n"
                    "Example: plan(action='accept_partial', step_id='step-3', "
                    "reason='delegate exhausted budget but API exists', "
                    "notes='verified: backend/src/app.ts, routes/...')"
                ),
                is_error=True,
            )
        if not notes:
            return ToolResult(
                content=(
                    "Error: 'notes' required — list verified files/artifacts "
                    "that prove partial completion."
                ),
                is_error=True,
            )
        if not reason:
            return ToolResult(
                content=(
                    "Error: 'reason' required — explain why accepting partial "
                    "work is correct (e.g. delegate terminated but schema exists)."
                ),
                is_error=True,
            )

        step = plan.get_step(step_id) or self._fuzzy_match_step(plan, step_id)
        if step is None:
            return ToolResult(
                content=f"No step '{step_id}' in plan.",
                is_error=True,
            )

        has_failed = self._step_has_failed_delegate(plan, step)
        member = self._step_delegate_member(plan, step)
        verified, vdetail = self._verify_step_artifacts(plan, step)

        if not has_failed:
            if member is not None and member.status == "done":
                return ToolResult(
                    content=(
                        f"Step '{step.id}' already completed by delegate "
                        f"#{member.delegate_number}. "
                        "Use plan(action='update', status='done', notes='...')."
                    ),
                    is_error=True,
                )
            if not verified:
                expected = ", ".join(step.output_files) if step.output_files else "(none declared)"
                return ToolResult(
                    content=(
                        f"Step '{step.id}' has no failed delegate and artifacts "
                        f"are not verified on disk.\n"
                        f"Expected output_files: {expected}\n"
                        "Use read/list_dir to confirm files exist, then retry "
                        "accept_partial with notes listing verified paths."
                    ),
                    is_error=True,
                )
            tag = "[verified_on_disk]"
            reason_line = reason or vdetail
        else:
            tag = "[accept_partial]"
            reason_line = reason

        step.status = "done"
        step.notes = f"{tag} {reason_line}\n{notes}"[:2000]
        self._store.save(plan)
        if self._team_manager is not None:
            try:
                for team in self._team_manager.list_teams(include_terminal=True):
                    if team.plan_id != plan.id:
                        continue
                    for member in team.members:
                        if (
                            member.step_id == step.id
                            and member.status in ("failed", "cancelled")
                        ):
                            member.status = "done"
                            member.result_summary = (
                                f"{tag} {reason_line}"[:500]
                            )
                            self._team_manager.save(team)
            except Exception:
                pass
        return ToolResult(
            content=(
                f"Accepted completion for '{step.id}': {step.label}\n"
                f"Reason: {reason_line}\n"
                f"Evidence: {notes[:500]}\n"
                f"Plan progress: {plan.progress_summary()}"
            ),
            details={
                "plan_id": plan.id,
                "step_id": step.id,
                "action": "accept_partial",
                "status": step.status,
                "verified_on_disk": tag == "[verified_on_disk]",
                "wave_needs_advance": True,
                "orchestrator_recovery": True,
            },
        )

    async def _continue_work(self, params: dict[str, Any]) -> ToolResult:
        """Import remaining steps from another root plan; archive the source."""
        target = self._store.find_active()
        if target is None:
            return ToolResult(
                content=(
                    "No active plan. Use plan(action='read') or plan(action='create') "
                    "first."
                ),
                is_error=True,
            )

        source_id = (params.get("source_plan_id") or params.get("plan_id") or "").strip()
        source: Plan | None = None
        if source_id:
            source = self._store.load(source_id)
        else:
            for candidate in self._store.find_active_roots():
                if candidate.id != target.id:
                    source = candidate
                    break
        if source is None:
            return ToolResult(
                content=(
                    "No source plan to import from. Pass source_plan_id=... "
                    "or use plan(action='add_step', plan_id=..., label='...') "
                    "for individual remaining steps."
                ),
                is_error=True,
            )
        if source.id == target.id:
            return ToolResult(
                content="source_plan_id must differ from the active plan.",
                is_error=True,
            )

        reason = (
            params.get("reason", "").strip()
            or f"continued into {target.id}"
        )
        imported: list[str] = []
        skipped: list[str] = []
        failed_for_subplan: list[str] = []
        existing_labels = {
            s.label.strip().lower() for s in target.steps
        }

        for step in source.steps:
            if step.status in ("done", "skipped"):
                continue
            label_lower = step.label.strip().lower()
            if label_lower in existing_labels:
                skipped.append(step.label)
                continue
            new_step = PlanStep(
                id=f"step-{len(target.steps) + 1}",
                label=step.label,
                description=step.description,
                status="pending",
                output_files=list(step.output_files),
                notes=step.notes,
                depends_on=list(step.depends_on),
                delegatable=step.delegatable,
            )
            target.steps.append(new_step)
            existing_labels.add(label_lower)
            imported.append(new_step.label)
            if step.status == "failed":
                failed_for_subplan.append(new_step.id)

        if not imported:
            return ToolResult(
                content=(
                    f"No steps imported from {source.id} "
                    f"(all done or duplicate labels).\n"
                    f"Active plan: {target.id}"
                ),
                is_error=True,
            )

        target.touch()
        self._store.save(target)
        self._store.archive(source.id, reason)

        sub_hint = ""
        if failed_for_subplan:
            sub_hint = (
                "\n\nFailed steps were copied as pending. For a focused retry on "
                "one failed area, use plan(action='sub_plan', "
                f"parent_step_id='{failed_for_subplan[0]}', title='...', "
                "steps=[...]) then team(create) on that sub-plan."
            )

        return ToolResult(
            content=(
                f"Continued work into active plan {target.id} from {source.id}.\n"
                f"Imported {len(imported)} step(s): {', '.join(imported[:8])}"
                + (f" (skipped duplicates: {', '.join(skipped[:5])})" if skipped else "")
                + f"\nArchived source plan {source.id} ({reason})."
                + sub_hint
                + f"\nProgress: {target.progress_summary()}"
            ),
            details={
                "plan_id": target.id,
                "source_plan_id": source.id,
                "action": "continue_work",
                "imported": imported,
            },
        )

    async def _add_step(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        label = params.get("label", "").strip()
        if not label:
            return ToolResult(
                content="Error: 'label' is required for add_step.",
                is_error=True,
            )

        # Duplicate guard: reject if a step with the same label already exists
        label_lower = label.lower()
        for existing in plan.steps:
            if existing.label.strip().lower() == label_lower:
                return ToolResult(
                    content=(
                        f"Step already exists: {existing.id} — "
                        f"'{existing.label}' ({existing.status}). "
                        f"Use plan(action='update', step_id='{existing.id}') "
                        f"to modify it."
                    ),
                    is_error=True,
                )

        next_idx = len(plan.steps) + 1
        step_id = f"step-{next_idx}"
        new_step = PlanStep(
            id=step_id,
            label=label,
            description=params.get("step_description") or "",
            output_files=params.get("output_files") or [],
            owned_paths=params.get("owned_paths") or [],
            depends_on=params.get("depends_on") or [],
            delegatable=bool(params.get("delegatable", False)),
        )
        plan.steps.append(new_step)
        _path_warnings = self._align_step_paths(plan)
        _path_note = self._format_path_warnings(_path_warnings)
        self._store.save(plan)

        step_lines = "\n".join(
            f"  - {s.id}: {s.label} ({s.status})"
            + (" [delegatable]" if s.delegatable else "")
            for s in plan.steps
        )
        dep_note = ""
        if new_step.depends_on:
            dep_note = f"\nDepends on: {', '.join(new_step.depends_on)}"

        return ToolResult(
            content=(
                f"Added step '{step_id}': {label}\n"
                f"Delegatable: {new_step.delegatable}{dep_note}\n"
                f"Plan '{plan.id}' now has {len(plan.steps)} step(s):\n"
                f"{step_lines}"
                + _path_note
            ),
            details={
                "plan_id": plan.id,
                "step_id": step_id,
                "action": "add_step",
            },
        )

    async def _sub_plan(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        parent_step_id = params.get("parent_step_id", "")
        title = params.get("title", "").strip()
        if not parent_step_id or not title:
            return ToolResult(
                content="Error: 'parent_step_id' and 'title' required.",
                is_error=True,
            )

        parent_step = plan.get_step(parent_step_id)
        if parent_step is None:
            parent_step = self._fuzzy_match_step(plan, parent_step_id)
        if parent_step is None:
            valid = ", ".join(
                f"{s.id} ({s.label})" for s in plan.steps
            )
            return ToolResult(
                content=f"No step '{parent_step_id}' in plan. Valid: {valid}",
                is_error=True,
            )
        parent_step_id = parent_step.id

        sub = self._store.create_sub_plan(
            parent_plan_id=plan.id,
            parent_step_id=parent_step_id,
            title=title,
            requirements=params.get("requirements", ""),
            tech_stack=self._parse_tech_stack(params.get("tech_stack")) or None,
            acceptance_criteria=params.get("acceptance_criteria"),
            steps=params.get("steps"),
            scaffolding=params.get("files"),
        )
        if sub is None:
            return ToolResult(
                content=(
                    f"Failed to create sub-plan under step '{parent_step_id}' "
                    f"in plan '{plan.id}'. The step exists but the plan store "
                    f"could not create the sub-plan (check disk/permissions or "
                    f"whether the step already has a sub_plan_id)."
                ),
                is_error=True,
            )

        sub.status = "in_progress"
        self._store.save(sub)

        return ToolResult(
            content=(
                f"Sub-plan created: {sub.id}\n"
                f"Parent: {plan.id} → step {parent_step_id}\n"
                f"Title: {sub.title}\n"
                f"Steps: {len(sub.steps)}\n"
                f"Delegate this: delegate(task='Execute sub-plan {sub.id}')"
            ),
            details={
                "plan_id": sub.id,
                "parent_plan_id": plan.id,
                "parent_step_id": parent_step_id,
                "action": "sub_plan",
            },
        )

    async def _verify(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        issues: list[str] = []

        # Disk truth checks (deterministic — not LLM-only)
        issues.extend(self._audit_empty_project_files(plan))
        issues.extend(self._audit_delegate_failures(plan))
        issues.extend(self._audit_tech_stack(plan))
        issues.extend(self._audit_local_tests(plan))

        # Check acceptance criteria vs. actual files
        for fname, info in plan.scaffolding.items():
            fpath = Path(self._workspace) / fname
            if not fpath.exists():
                issues.append(f"File missing: {fname} ({info.get('purpose', '')})")
            elif info.get("status") != "created":
                issues.append(f"File exists but not marked created: {fname}")

        # Check step completion
        incomplete = [s for s in plan.steps if s.status not in ("done", "skipped")]
        if incomplete:
            for s in incomplete:
                issues.append(f"Step not done: [{s.id}] {s.label} (status: {s.status})")

        # Check sub-plan completion
        for s in plan.steps:
            if s.sub_plan_id:
                sub = self._store.load(s.sub_plan_id)
                if sub and sub.status != "done":
                    issues.append(
                        f"Sub-plan {s.sub_plan_id} not complete "
                        f"(status: {sub.status})"
                    )

        # Read output files, check for stubs/TODOs and obvious incompleteness
        _STUB_MARKERS = [
            "// Add ", "// TODO", "// FIXME", "// HACK",
            "# TODO", "# FIXME", "# Add ", "# HACK",
            "pass  #", "/* TODO", "/* FIXME",
            "NotImplementedError", "raise NotImplemented",
        ]
        file_checks: list[str] = []
        for fname in plan.scaffolding:
            fpath = Path(self._workspace) / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    lines = content.count("\n") + (1 if content else 0)
                    size = len(content.encode("utf-8"))
                    file_checks.append(
                        f"  {fname}: {lines} lines, {size} bytes"
                    )
                    stubs = [
                        ln.strip()
                        for ln in content.split("\n")
                        if any(m in ln for m in _STUB_MARKERS)
                    ]
                    if stubs:
                        issues.append(
                            f"File '{fname}' contains {len(stubs)} "
                            f"stub/TODO marker(s) — likely incomplete: "
                            f"{stubs[0][:80]}"
                        )
                except Exception:
                    file_checks.append(f"  {fname}: (could not read)")

        # ── Semantic micro-inference: LLM checks code vs criteria ───
        _file_contents: dict[str, str] = {}
        for fname in plan.scaffolding:
            fpath = Path(self._workspace) / fname
            if fpath.exists():
                try:
                    _file_contents[fname] = fpath.read_text(
                        encoding="utf-8", errors="replace",
                    )[:8000]
                except Exception:
                    pass

        if (
            not issues
            and self._inference_fn is not None
            and plan.acceptance_criteria
            and _file_contents
        ):
            try:
                _verify_prompt = (
                    "You are a QA reviewer. Given the acceptance criteria "
                    "and the code below, list ONLY concrete issues where "
                    "the code does NOT meet a criterion. If a criterion is "
                    "fully met, skip it. Output format:\n"
                    "ISSUE: <criterion number> - <short description>\n"
                    "If everything is fine, output: ALL_CRITERIA_MET\n\n"
                    "ACCEPTANCE CRITERIA:\n"
                )
                for i, c in enumerate(plan.acceptance_criteria, 1):
                    _verify_prompt += f"  {i}. {c}\n"
                _verify_prompt += "\nFILES:\n"
                for fn, fc in _file_contents.items():
                    _verify_prompt += f"\n--- {fn} ---\n{fc}\n"

                _llm_result = await self._inference_fn(_verify_prompt)
                if _llm_result and "ALL_CRITERIA_MET" not in _llm_result:
                    for line in _llm_result.strip().split("\n"):
                        line = line.strip()
                        if line.startswith("ISSUE:"):
                            issues.append(
                                f"[Code review] {line[6:].strip()}"
                            )
                    if issues:
                        logger.info(
                            "Plan %s: micro-inference found %d issue(s)",
                            plan.id, len(issues),
                        )
            except Exception as _inf_exc:
                logger.debug(
                    "Plan %s: micro-inference skipped: %s",
                    plan.id, _inf_exc,
                )

        # Build verification prompt for the model
        plan.audit.last_verified_at = _time.time()
        plan.audit.issues = issues
        plan.audit.all_criteria_met = len(issues) == 0
        self._store.save(plan)

        parts = [f"VERIFICATION AUDIT for plan {plan.id}: {plan.title}\n"]

        if plan.acceptance_criteria:
            parts.append("Acceptance Criteria to check:")
            for i, c in enumerate(plan.acceptance_criteria, 1):
                parts.append(f"  {i}. {c}")
            parts.append("")

        if file_checks:
            parts.append("Output files found:")
            parts.extend(file_checks)
            parts.append("")

        if issues:
            parts.append(f"ISSUES FOUND ({len(issues)}):")
            for issue in issues:
                parts.append(f"  - {issue}")
            parts.append(
                "\nFix these issues before marking the plan complete. "
                "Read each output file and verify it meets the criteria."
            )
        else:
            parts.append(
                "No structural issues found. All acceptance criteria "
                "verified. Call plan(action='complete') to finish."
            )

        return ToolResult(
            content="\n".join(parts),
            details={
                "plan_id": plan.id,
                "action": "verify",
                "issues": issues,
                "all_criteria_met": plan.audit.all_criteria_met,
            },
        )

    async def _complete(self, params: dict[str, Any]) -> ToolResult:
        from nls.agentic.plan_work import can_complete_plan, completion_gate_message

        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        if params.get("force", False):
            return ToolResult(
                content=(
                    "plan(action='complete', force=true) is not allowed. "
                    "Every step must reach status=done (use accept_partial after "
                    "a failed wave, or delegate/sub_plan to finish work), then "
                    "plan(action='verify'), then plan(action='complete')."
                ),
                is_error=True,
            )

        gate = completion_gate_message(plan, self._team_manager)
        if gate is not None:
            return ToolResult(content=gate, is_error=True)

        if not can_complete_plan(plan, self._team_manager):
            return ToolResult(
                content=completion_gate_message(plan, self._team_manager)
                or "Cannot complete plan.",
                is_error=True,
            )

        plan.status = "done"
        self._store.save(plan)
        self.clear_plan_context()

        if plan.parent_id:
            parent = self._store.load(plan.parent_id)
            if parent:
                for s in parent.steps:
                    if s.sub_plan_id == plan.id:
                        s.status = "done"
                        s.notes = f"Sub-plan {plan.id} completed"
                        break
                self._store.save(parent)

        # Auto-complete the linked todo item so the Kanban card syncs.
        _todo_note = ""
        if plan.todo_id and self._todo_complete_fn is not None:
            try:
                await self._todo_complete_fn(plan.todo_id)
                _todo_note = f"\nLinked todo {plan.todo_id} marked done."
            except Exception as _te:
                logger.debug("Auto-complete todo %s failed: %s", plan.todo_id, _te)

        # Reset CWD to workspace root so subsequent writes (research notes,
        # reports, new projects) are not placed inside the completed project.
        if self._cwd_reset_fn is not None:
            try:
                self._cwd_reset_fn(self._workspace)
                logger.info(
                    "Orchestrator CWD reset to workspace root after plan %s",
                    plan.id,
                )
            except Exception as _ce:
                logger.debug("CWD reset failed: %s", _ce)

        if self._team_manager is not None:
            try:
                self._team_manager.cleanup_plan_checkbacks(plan.id)
            except Exception as exc:
                logger.debug(
                    "Plan %s: check-back cleanup failed: %s", plan.id, exc,
                )

        return ToolResult(
            content=(
                f"Plan {plan.id} marked as DONE.\n"
                f"Title: {plan.title}\n"
                f"Progress: {plan.progress_summary()}\n"
                + (f"Parent plan {plan.parent_id} step updated."
                   if plan.parent_id else "")
                + _todo_note
            ),
            details={
                "plan_id": plan.id,
                "action": "complete",
                "todo_id": plan.todo_id,
                "parent_id": plan.parent_id or "",
            },
        )

    async def _fix_dependencies(self, params: dict[str, Any]) -> ToolResult:
        """Re-run dependency inference and break common service↔API cycles."""
        from nls.agentic.plan_store import (
            break_service_before_api_edges,
            detect_dependency_cycles,
            format_dependency_cycle_hints,
            get_delegation_waves,
        )

        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        cycles_before = detect_dependency_cycles(plan)
        _infer_note = await self._infer_dependencies(plan)
        _safety = self._store.ensure_dependency_safety_net(plan)
        _broken = break_service_before_api_edges(plan)
        cycles_after = detect_dependency_cycles(plan)
        self._store.save(plan)

        waves = get_delegation_waves(plan)
        wave_sizes = [len(w) for w in waves]
        parts = [
            f"Dependency graph repaired for plan {plan.id}.",
            f"Waves ({len(waves)}): {' → '.join(str(s) for s in wave_sizes)}",
        ]
        if _infer_note:
            parts.append(f"Inference: {_infer_note}")
        if _safety:
            parts.append(f"Safety net patched {_safety} step(s).")
        if _broken:
            parts.append(
                f"Removed {_broken} service→API edge(s) "
                "(modules must not depend on the route layer)."
            )
        if cycles_before and not cycles_after:
            parts.append("Resolved dependency cycle(s).")
        elif cycles_after:
            parts.append(format_dependency_cycle_hints(plan))
            parts.append(
                "Manual fix: plan(action='update', step_id='...', "
                "depends_on=[...]) on the steps listed above."
            )
        else:
            parts.append("No cycles detected.")

        parts.append(
            "\nNEXT: team(action='create', plan_id='"
            f"{plan.id}', wave=N) → team(action='launch') for pending "
            "delegatable steps. Do NOT plan(delete) or plan(create) from scratch."
        )

        return ToolResult(
            content="\n".join(parts),
            details={
                "plan_id": plan.id,
                "action": "fix_dependencies",
                "cycles_remaining": len(cycles_after),
                "waves": wave_sizes,
            },
        )

    async def _delete(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found to delete.", is_error=True)

        reason = params.get("reason", "").strip() or "Agent requested deletion"

        done_steps = [s for s in plan.steps if s.status == "done"]
        pending_steps = [
            s for s in plan.steps
            if s.status in ("pending", "in_progress", "failed")
        ]
        if (
            done_steps
            and pending_steps
            and not params.get("force_delete")
        ):
            from nls.agentic.plan_store import format_dependency_cycle_hints
            _pending_labels = ", ".join(
                f'"{s.label}"' for s in pending_steps[:5]
            )
            _hint = (
                f"BLOCKED: plan {plan.id} has {len(done_steps)} done step(s) and "
                f"{len(pending_steps)} still open ({_pending_labels}).\n"
                "Deleting would skip all remaining work and lose Kanban history.\n\n"
                "Prefer instead:\n"
                f"  • plan(action='fix_dependencies', plan_id='{plan.id}') "
                "if team(launch) is blocked by dependencies\n"
                f"  • plan(action='update', step_id='...', depends_on=[...]) "
                "to fix one step's graph\n"
                f"  • plan(action='continue_work', source_plan_id='...') "
                "to import remainder into another plan\n"
                "  • team(action='advance') then launch the next wave\n\n"
                "Only use plan(delete) with force_delete=true when the user "
                "explicitly abandons this plan."
            )
            _cycle = format_dependency_cycle_hints(plan)
            if _cycle:
                _hint += f"\n\n{_cycle}"
            return ToolResult(content=_hint, is_error=True)

        # Cancel any teams linked to this plan
        _cancelled_teams: list[str] = []
        if self._team_manager is not None:
            try:
                all_teams = self._team_manager.list_teams(include_terminal=False)
                for t in all_teams:
                    if t.plan_id == plan.id and not t.is_terminal:
                        await self._team_manager.disband_team(t.id)
                        _cancelled_teams.append(f"{t.name} [{t.id}]")
            except Exception as exc:
                logger.warning("Failed to cancel teams for plan %s: %s", plan.id, exc)

        # Archive sub-plans too
        _archived_subs: list[str] = []
        for step in plan.steps:
            if step.sub_plan_id:
                sub = self._store.archive(step.sub_plan_id, reason)
                if sub:
                    _archived_subs.append(step.sub_plan_id)

        archived = self._store.archive(plan.id, reason)
        if archived is None:
            return ToolResult(content=f"Failed to archive plan {plan.id}.", is_error=True)

        parts = [
            f"Plan {plan.id} ARCHIVED.",
            f"Title: {plan.title}",
            f"Reason: {reason}",
            f"Steps skipped: {sum(1 for s in archived.steps if s.status == 'skipped')}",
        ]
        if _cancelled_teams:
            parts.append(f"Cancelled teams: {', '.join(_cancelled_teams)}")
        if _archived_subs:
            parts.append(f"Archived sub-plans: {', '.join(_archived_subs)}")
        parts.append(
            "\nRecovery options (prefer these over plan(create) from scratch):\n"
            f"  • plan(action='continue_work', source_plan_id='{plan.id}') "
            "to import remaining steps into your active plan\n"
            "  • plan(action='fix_dependencies') on the active plan if launch blocked\n"
            "  • Solo IC work in switch_mode(evaluating) for small gaps only"
        )

        return ToolResult(
            content="\n".join(parts),
            details={
                "plan_id": plan.id,
                "action": "delete",
                "reason": reason,
                "cancelled_teams": _cancelled_teams,
                "archived_sub_plans": _archived_subs,
                "orchestrator_recovery": True,
            },
        )

    async def _delegate(self, params: dict[str, Any]) -> ToolResult:
        """Validate delegation readiness for a step or sub-plan.

        Returns an instruction dict that the executor can use to spawn
        a sub-agent. The actual spawning happens in the orchestrator.
        """
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        step_id = params.get("step_id", "").strip()
        if not step_id:
            return ToolResult(
                content="Error: 'step_id' is required for delegate.",
                is_error=True,
            )

        step = plan.get_step(step_id) or PlanTool._fuzzy_match_step(plan, step_id)
        if step is None:
            return ToolResult(
                content=f"Step '{step_id}' not found in plan {plan.id}.",
                is_error=True,
            )

        if not step.delegatable:
            return ToolResult(
                content=(
                    f"Step '{step.id}' is not marked as delegatable. "
                    f"Set delegatable=true in the plan step to enable delegation."
                ),
                is_error=True,
            )

        # Check dependencies are satisfied (handles both IDs and labels)
        unsatisfied = self._unsatisfied_dependencies(plan, step)

        if unsatisfied:
            return ToolResult(
                content=(
                    f"Cannot delegate step '{step.id}': unmet dependencies:\n"
                    + "\n".join(f"  - {u}" for u in unsatisfied)
                ),
                is_error=True,
            )

        sub_plan_context = ""
        if step.sub_plan_id:
            sub = self._store.load(step.sub_plan_id)
            if sub:
                sub_plan_context = sub.to_context_string()

        step.status = "in_progress"
        self._store.save(plan)

        return ToolResult(
            content=(
                f"Step '{step.id}' ({step.label}) ready for delegation.\n"
                f"Sub-plan: {step.sub_plan_id or '(none — direct delegation)'}\n"
                f"The executor should now spawn a sub-agent for this step."
            ),
            details={
                "plan_id": plan.id,
                "step_id": step.id,
                "action": "delegate",
                "sub_plan_id": step.sub_plan_id,
                "sub_plan_context": sub_plan_context,
                "task": step.label,
            },
        )

    # -- Helpers -------------------------------------------------------

    @staticmethod
    def _unsatisfied_dependencies(plan: Plan, step: PlanStep) -> list[str]:
        """Return human-readable unmet dependency refs for *step*."""
        step_map = {s.id: s for s in plan.steps}
        label_map = {s.label.lower().strip(): s for s in plan.steps}
        unsatisfied: list[str] = []
        for dep_ref in step.depends_on:
            dep_step = step_map.get(dep_ref)
            if dep_step is None:
                needle = dep_ref.lower().strip()
                dep_step = label_map.get(needle)
                if dep_step is None:
                    for lbl, s in label_map.items():
                        if lbl.startswith(needle) or needle.startswith(lbl):
                            dep_step = s
                            break
            if dep_step is None:
                unsatisfied.append(f"{dep_ref}: (unknown step — not in plan)")
            elif dep_step.status not in ("done", "skipped"):
                unsatisfied.append(f"{dep_ref}: {dep_step.label} ({dep_step.status})")
        return unsatisfied

    _STEP_NUM_RE = re.compile(r"^step[-_ ]?(\d+)$", re.IGNORECASE)

    @staticmethod
    def _fuzzy_match_step(plan: Plan, step_id: str) -> PlanStep | None:
        """Try to resolve a step_id that doesn't match any ID exactly.

        Handles common LLM mistakes: passing the label text, a numeric
        index, ``step-2`` vs ``step_2``, or a partial ID prefix.
        """

        if not plan.steps:
            return None

        # 1. Numeric index — prefer 1-based (LLMs say "step 1" for first)
        try:
            idx = int(step_id)
            if 1 <= idx <= len(plan.steps):
                return plan.steps[idx - 1]
            if idx == 0:
                return plan.steps[0]
        except ValueError:
            pass

        # 2. step-N / step_N / stepN patterns (normalise separator)
        m = PlanTool._STEP_NUM_RE.match(step_id.strip())
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(plan.steps):
                return plan.steps[idx - 1]
            if idx == 0:
                return plan.steps[0]

        # 3. Normalised ID match (dash ↔ underscore)
        normalised = step_id.strip().replace("_", "-").lower()
        for s in plan.steps:
            if s.id.replace("_", "-").lower() == normalised:
                return s

        # 4. Label match (case-insensitive, strip whitespace)
        needle = step_id.strip().lower()
        for s in plan.steps:
            if s.label.strip().lower() == needle:
                return s

        # 5. Label substring match (if needle is long enough to be unambiguous)
        if len(needle) > 10:
            matches = [
                s for s in plan.steps
                if needle in s.label.strip().lower()
            ]
            if len(matches) == 1:
                return matches[0]

        # 6. ID prefix match
        prefix = step_id.replace("-", "_") if "-" in step_id else step_id
        if prefix.startswith("step_"):
            matches = [s for s in plan.steps if s.id.startswith(prefix)]
            if len(matches) == 1:
                return matches[0]

        return None

    def _resolve_plan(self, params: dict[str, Any]) -> Plan | None:
        plan_id = (params.get("plan_id") or "").strip()
        return self._store.resolve_work_plan(
            plan_id,
            self._team_manager,
            reopen=True,
        )

    def register_output_file(self, file_path: str) -> None:
        """Auto-register a file in the active plan's scaffolding."""
        plan = self._store.resolve_work_plan("", self._team_manager, reopen=False)
        if plan is None or not file_path:
            return
        try:
            _fp = Path(file_path).resolve()
            _ws = Path(self._workspace).resolve()
            rel = str(_fp.relative_to(_ws))
        except (ValueError, TypeError):
            rel = file_path
        if rel not in plan.scaffolding:
            plan.scaffolding[rel] = {
                "purpose": "auto-registered",
                "status": "created",
            }
        else:
            plan.scaffolding[rel]["status"] = "created"
        self._store.save(plan)

    # Tool call pattern: matches tool_name(... in onboarding instructions
    _TOOL_CALL_RE = __import__("re").compile(
        r"\b([a-z][a-z0-9_]+)\(\s*action\s*=\s*['\"](\w+)['\"]"
    )

    # Tools that are always implicit and should not be checked
    _IMPLICIT_TOOLS = frozenset({
        "plan", "ask_user", "browser",
    })

    def _check_required_tools(
        self,
        plan: Plan,
        step: PlanStep,
    ) -> list[str]:
        """Check if the SKILL ONBOARDING context references tool calls
        for this step that the agent hasn't invoked yet.

        Returns a list of missing tool names, or empty if all satisfied.
        """
        if self._invoked_tools is None:
            return []
        if not self._onboarding_context:
            return []

        # Find the step's index in the plan
        step_idx = next(
            (i for i, s in enumerate(plan.steps) if s.id == step.id),
            -1,
        )
        if step_idx < 0:
            return []

        # Extract the section for this step from the SKILL ONBOARDING.
        # Onboarding text uses "STEP N" markers.  Numbering may be
        # 0-based or 1-based, so try both.
        onboard = self._onboarding_context

        def _find_step_marker(num: int, after: int = 0) -> int:
            for m in [f"STEP {num} ", f"STEP {num}\n",
                       f"STEP {num}—", f"STEP {num} —"]:
                pos = onboard.upper().find(m.upper(), after)
                if pos >= 0:
                    return pos
            return -1

        start = _find_step_marker(step_idx)
        if start < 0:
            start = _find_step_marker(step_idx + 1)
        if start < 0:
            return []

        end_0 = _find_step_marker(step_idx + 1, start + 1)
        end_1 = _find_step_marker(step_idx + 2, start + 1)
        end = end_0 if end_0 > start else (end_1 if end_1 > start else -1)

        section = onboard[start:end] if end >= 0 else onboard[start:]

        # Find all tool(action=...) references in the section
        referenced_tools = set()
        for match in self._TOOL_CALL_RE.finditer(section):
            tool_name = match.group(1)
            if tool_name not in self._IMPLICIT_TOOLS:
                referenced_tools.add(tool_name)

        if not referenced_tools:
            return []

        missing = [t for t in referenced_tools if t not in self._invoked_tools]
        if missing:
            logger.warning(
                "Plan step '%s' references tools %s but only %s "
                "were invoked — blocking premature completion",
                step.id, referenced_tools,
                self._invoked_tools & referenced_tools,
            )
        return missing

    def get_store(self) -> PlanStore:
        return self._store


class PlanReadOnlyTool:
    """Read-only plan access for sub-agents.

    Shares the same :class:`PlanStore` directory so sub-agents can read
    plans (including sub-plans created by the orchestrator), but the
    schema only exposes ``action='read'`` — sub-agents cannot create,
    update, or complete plans.
    """

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._store = PlanStore(workspace)

    @property
    def name(self) -> str:
        return "plan"

    @property
    def description(self) -> str:
        return (
            "Read your assigned plan. Use plan(action='read') or "
            "plan(action='read', plan_id='...') to see your task "
            "instructions and steps. You cannot create or modify plans "
            "— the orchestrator manages the plan lifecycle."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read"],
                    "description": "Read the plan.",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID to read (defaults to active plan).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        plan_id = params.get("plan_id", "")
        plan = self._store.load(plan_id) if plan_id else self._store.find_active()
        if plan is None:
            return ToolResult(content="No plan found.", is_error=True)
        return ToolResult(
            content=plan.to_context_string(),
            details={"plan_id": plan.id, "action": "read"},
        )

    def get_store(self) -> PlanStore:
        return self._store


def create_plan_tool(
    workspace: str,
    inference_fn: Any | None = None,
    dep_inference_fn: Any | None = None,
) -> PlanTool:
    """Factory: create a plan tool configured for an agent's workspace."""
    tool = PlanTool(workspace)
    if inference_fn is not None:
        tool.set_inference_fn(inference_fn)
    if dep_inference_fn is not None:
        tool.set_dep_inference_fn(dep_inference_fn)
    return tool
