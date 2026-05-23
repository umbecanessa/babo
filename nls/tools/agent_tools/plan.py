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
    create   -- Create a new plan with title, requirements, steps
    read     -- Read the current active plan
    update   -- Update a step's status or notes
    add_step -- Add a new step to an existing plan
    sub_plan -- Create a linked sub-plan for a complex step
    verify   -- Trigger verification audit against acceptance criteria
    complete -- Mark plan as done, sync linked todo
    delete   -- Archive a stale/wrong plan (cancels linked teams)
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
            "Actions: 'create' (new plan — pass todo_id and optionally steps=[...]), "
            "'read' (current plan), "
            "'update' (mark step done/in-progress), "
            "'add_step' (add a step to an existing plan — pass label, delegatable), "
            "'sub_plan' (linked JSON sub-plan for a complex step), "
            "'verify' (audit output against acceptance criteria), "
            "'complete' (mark plan done, auto-completes linked todo), "
            "'delegate' (mark a step as delegated to a sub-agent), "
            "'delete' (archive a stale/wrong plan — cancels linked teams, "
            "frees you to create a new one; pass reason='...'). "
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
                        "sub_plan", "verify", "complete",
                        "delegate", "delete",
                    ],
                    "description": "The action to perform.",
                },
                "title": {
                    "type": "string",
                    "description": "Plan title (required for create/sub_plan). Pass '-' for other actions.",
                },
                "requirements": {
                    "type": "string",
                    "description": "Full task requirements (for create/sub_plan).",
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
                    "description": "Force completion even if steps are pending (for complete action). Pending steps will be auto-skipped.",
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
                    "description": "Step IDs or labels this step depends on (for add_step).",
                },
                "output_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Expected output files for a step (for add_step).",
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
            "add_step": self._add_step,
            "sub_plan": self._sub_plan,
            "verify": self._verify,
            "complete": self._complete,
            "delegate": self._delegate,
            "delete": self._delete,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(
                content=f"Unknown plan action: '{action}'. "
                f"Use one of: {', '.join(dispatch.keys())}",
                is_error=True,
            )
        return await handler(params)

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
                    f"Or use plan(action='sub_plan') for complex sub-tasks.\n"
                    f"If this plan is stale or wrong, delete it first with "
                    f"plan(action='delete', plan_id='{existing.id}', "
                    f"reason='...').\n"
                    f"If the user asked for a completely SEPARATE project, "
                    f"pass force_new=true."
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
            acceptance_criteria=params.get("acceptance_criteria"),
            steps=params.get("steps"),
            scaffolding=params.get("files"),
            todo_id=params.get("todo_id"),
            project_dir=params.get("project_dir") or _reuse_dir,
        )

        # Micro-inference: validate and fix dependency graph.
        _dep_warning = await self._infer_dependencies(plan)

        plan.status = "in_progress"
        self._store.save(plan)

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
                f"plan(action='read') to review, then fix with "
                f"plan(action='add_step') or re-create the plan with "
                f"proper depends_on arrays.\n"
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

        return ToolResult(
            content=(
                f"Plan created: {plan.id}\n"
                f"Title: {plan.title}\n"
                + _dir_note
                + f"Steps ({len(plan.steps)}):\n{step_lines}\n"
                f"Criteria: {len(plan.acceptance_criteria)}\n"
                f"Saved to: .plans/{plan.id}.json\n\n"
                f"Use the step IDs above when updating steps.\n"
                f"Read the plan before each step to stay grounded."
                + _todo_note
                + _dep_note
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
                    self._store.save(plan)
                else:
                    logger.info(
                        "PlanTool: dependency inference validated graph "
                        "(no changes needed)%s",
                        f" | diagnostics: {diagnosis}" if diagnosis else "",
                    )
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
                    f"If you haven't verified the result yet, do that first."
                ),
                is_error=True,
            )

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

        new_desc = params.get("step_description")
        new_project_dir = (params.get("project_dir") or "").strip()

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

        # Auto-update scaffolding status based on step output_files
        if new_status == "done" and step.output_files:
            for fname in step.output_files:
                if fname in plan.scaffolding:
                    plan.scaffolding[fname]["status"] = "created"

        self._store.save(plan)

        return ToolResult(
            content=(
                f"Updated step '{resolved_id}': {step.label}\n"
                f"Status: {step.status}\n"
                f"Plan progress: {plan.progress_summary()}"
            ),
            details={
                "plan_id": plan.id,
                "step_id": resolved_id,
                "action": "update",
                "status": step.status,
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
            depends_on=params.get("depends_on") or [],
            delegatable=bool(params.get("delegatable", False)),
        )
        plan.steps.append(new_step)
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
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found.", is_error=True)

        if (
            hasattr(plan, "audit")
            and plan.audit is not None
            and getattr(plan.audit, "last_verified_at", None) is None
            and not params.get("force", False)
        ):
            return ToolResult(
                content=(
                    "Cannot complete plan without verification. "
                    "Call plan(action='verify') first."
                ),
                is_error=True,
            )

        pending = [s for s in plan.steps if s.status not in ("done", "skipped")]
        force = params.get("force", False)

        if pending and not force:
            labels = "\n".join(
                f"  - [{s.id}] {s.label} ({s.status})" for s in pending
            )
            return ToolResult(
                content=(
                    f"Cannot complete plan {plan.id}: "
                    f"{len(pending)} step(s) still pending:\n{labels}\n\n"
                    "Either finish remaining steps, mark them as skipped with "
                    "plan(action='update', step_id='...', status='skipped', "
                    "notes='reason'), or use plan(action='complete', force=true) "
                    "to force completion."
                ),
                is_error=True,
            )

        if pending and force:
            for s in pending:
                s.status = "skipped"
                s.notes = (s.notes + " [auto-skipped on force complete]").strip()

        plan.status = "done"
        self._store.save(plan)

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

    async def _delete(self, params: dict[str, Any]) -> ToolResult:
        plan = self._resolve_plan(params)
        if plan is None:
            return ToolResult(content="No active plan found to delete.", is_error=True)

        reason = params.get("reason", "").strip() or "Agent requested deletion"

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
        parts.append("\nYou can now create a fresh plan with plan(action='create', ...).")

        return ToolResult(
            content="\n".join(parts),
            details={
                "plan_id": plan.id,
                "action": "delete",
                "reason": reason,
                "cancelled_teams": _cancelled_teams,
                "archived_sub_plans": _archived_subs,
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
        step_map = {s.id: s for s in plan.steps}
        label_map = {s.label.lower().strip(): s for s in plan.steps}
        unsatisfied = []
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
        plan_id = params.get("plan_id", "")
        if plan_id:
            return self._store.load(plan_id)
        return self._store.find_active()

    def register_output_file(self, file_path: str) -> None:
        """Auto-register a file in the active plan's scaffolding."""
        plan = self._store.find_active()
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
