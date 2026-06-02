"""Structured Plan Store -- persistent hierarchical project plans.

Plans are JSON files stored in ``{workspace}/.plans/plan_{id}.json``.
Each plan contains:

- A task block (title, requirements, acceptance criteria)
- A scaffolding map (expected files and their purpose)
- Ordered steps with status tracking and sub-plan links
- An audit section for verification results

Plans form a tree: a parent plan's steps can reference sub-plans via
``sub_plan_id``, and sub-plans reference their parent via ``parent_id``.
Working Memory stores only the root plan reference; the agent navigates
the tree by reading the plan files.
"""

from __future__ import annotations

import json
import logging
import re as _re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nls.agentic.orchestration_profile_spec import (
    is_solo_execution_profile,
    plan_step_delegatable_default,
    should_auto_mark_delegatable,
)

logger = logging.getLogger(__name__)


PLAN_STATUSES = ("planning", "in_progress", "done", "blocked", "failed", "archived")
STEP_STATUSES = ("pending", "in_progress", "done", "skipped", "failed")


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


# -------------------------------------------------------------------
# Data model
# -------------------------------------------------------------------


@dataclass
class PlanStep:
    id: str = ""
    label: str = ""
    description: str = ""
    status: str = "pending"
    sub_plan_id: str | None = None
    output_files: list[str] = field(default_factory=list)
    owned_paths: list[str] = field(default_factory=list)
    notes: str = ""
    depends_on: list[str] = field(default_factory=list)
    delegatable: bool = False

    def __post_init__(self):
        pass

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("depends_on"):
            d.pop("depends_on", None)
        if not d.get("delegatable"):
            d.pop("delegatable", None)
        if not d.get("description"):
            d.pop("description", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        _label = d.get("label") or d.get("description") or ""
        return cls(
            id=d.get("id", ""),
            label=_label,
            description=d.get("description") or "",
            status=d.get("status", "pending"),
            sub_plan_id=d.get("sub_plan_id"),
            output_files=d.get("output_files") or [],
            owned_paths=d.get("owned_paths") or [],
            notes=d.get("notes") or "",
            depends_on=d.get("depends_on") or [],
            delegatable=d.get("delegatable", False),
        )


@dataclass
class PlanAudit:
    last_verified_at: float | None = None
    issues: list[str] = field(default_factory=list)
    all_criteria_met: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanAudit:
        return cls(
            last_verified_at=d.get("last_verified_at"),
            issues=d.get("issues", []),
            all_criteria_met=d.get("all_criteria_met", False),
        )


_FILLER_WORDS = frozenset({
    "set", "up", "setup", "create", "build", "implement", "develop",
    "make", "add", "the", "a", "an", "for", "and", "with", "project",
    "structure", "system", "application", "complete", "new", "full",
    "initial", "initialize", "configuration", "configure",
    "scaffolding", "scaffold", "core", "main", "basic", "starter",
    "boilerplate", "template", "foundation", "base", "primary",
})


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert a title to a short filesystem-safe directory name."""
    s = text.strip().lower()
    s = _re.sub(r"[^\w\s-]", "", s)
    words = _re.split(r"[\s_-]+", s)
    meaningful = [w for w in words if w and w not in _FILLER_WORDS]
    if not meaningful:
        meaningful = [w for w in words if w][:3]
    slug = "-".join(meaningful)
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "project"


@dataclass
class Plan:
    id: str = ""
    version: str = "1.0"
    created_at: float = 0.0
    updated_at: float = 0.0
    status: str = "planning"

    parent_id: str | None = None
    todo_id: str | None = None

    title: str = ""
    requirements: str = ""
    tech_stack: dict[str, str] = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)

    scaffolding: dict[str, dict[str, str]] = field(default_factory=dict)
    steps: list[PlanStep] = field(default_factory=list)
    audit: PlanAudit = field(default_factory=PlanAudit)

    project_dir: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"plan_{_short_id()}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def touch(self) -> None:
        self.updated_at = time.time()

    # -- Queries -------------------------------------------------------

    def get_step(self, step_id: str) -> PlanStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status in ("pending", "in_progress")]

    def all_steps_done(self) -> bool:
        return all(s.status in ("done", "skipped") for s in self.steps)

    def progress_summary(self) -> str:
        done = sum(1 for s in self.steps if s.status == "done")
        total = len(self.steps)
        return f"{done}/{total} steps done"

    def to_position_string(self) -> str:
        """Render a compact sliding-window view: previous + current + next.

        Gives the agent a focused sense of where it is in the plan
        without dumping the entire step list into context.
        """
        if not self.steps:
            return ""

        done_count = sum(1 for s in self.steps if s.status in ("done", "skipped"))
        total = len(self.steps)

        # Find the current step (first that isn't done/skipped)
        current_idx: int | None = None
        for i, s in enumerate(self.steps):
            if s.status not in ("done", "skipped"):
                current_idx = i
                break

        parts = [f"[PLAN POSITION — {done_count}/{total} steps done]"]
        parts.append(f"Task: {self.title}")

        if current_idx is None:
            # All steps done
            last = self.steps[-1]
            note = f" — {last.notes}" if last.notes else ""
            parts.append(f"  All steps complete. Last: \"{last.label}\"{note}")
        else:
            # Previous step (last completed before current)
            if current_idx > 0:
                prev = self.steps[current_idx - 1]
                note = f" ({prev.notes})" if prev.notes else ""
                parts.append(f"  Done:    \"{prev.label}\"{note}")

            # Current step
            cur = self.steps[current_idx]
            parts.append(f"  Current: \"{cur.label}\" [{cur.status}]")

            # Next step (first pending after current)
            if current_idx + 1 < total:
                nxt = self.steps[current_idx + 1]
                parts.append(f"  Next:    \"{nxt.label}\"")

            # Remaining count if more than 3 steps ahead
            remaining = total - done_count
            if remaining > 2:
                parts.append(f"  ({remaining} steps remaining)")

        return "\n".join(parts)

    def to_context_string(self) -> str:
        """Render plan as a concise context block for prompt injection."""
        parts = [
            f"[ACTIVE PLAN: {self.id}]",
            f"Task: {self.title}",
        ]
        if self.project_dir:
            parts.append(f"Project directory: {self.project_dir}/")
        if self.tech_stack:
            parts.append("Tech stack (mandatory):")
            for k, v in self.tech_stack.items():
                if v:
                    parts.append(f"  - {k}: {v}")
        if self.requirements:
            parts.append(f"Requirements: {self.requirements[:300]}")
        if self.acceptance_criteria:
            parts.append("Acceptance Criteria:")
            for i, c in enumerate(self.acceptance_criteria, 1):
                parts.append(f"  {i}. {c}")
        if self.scaffolding:
            parts.append("Files:")
            for fname, info in self.scaffolding.items():
                st = info.get("status", "pending")
                parts.append(f"  - {fname} ({st}): {info.get('purpose', '')}")
        if self.steps:
            parts.append(f"Steps ({self.progress_summary()}):")
            for s in self.steps:
                marker = {"done": "[x]", "in_progress": "[>]", "failed": "[!]",
                          "skipped": "[-]"}.get(s.status, "[ ]")
                sub = f" (sub-plan: {s.sub_plan_id})" if s.sub_plan_id else ""
                deps = f" (depends: {', '.join(s.depends_on)})" if s.depends_on else ""
                dlg = " [delegatable]" if s.delegatable else ""
                parts.append(f"  {marker} [{s.id}] {s.label}{sub}{deps}{dlg}")
                if s.description:
                    parts.append(f"      desc: {s.description}")
                if s.notes:
                    parts.append(f"      note: {s.notes}")
        if self.audit.issues:
            parts.append("Audit Issues:")
            for issue in self.audit.issues:
                parts.append(f"  - {issue}")
        parts.append(f"[END PLAN — status: {self.status}]")
        return "\n".join(parts)

    # -- Serialization -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "parent_id": self.parent_id,
            "todo_id": self.todo_id,
            "task": {
                "title": self.title,
                "requirements": self.requirements,
                "tech_stack": self.tech_stack,
                "acceptance_criteria": self.acceptance_criteria,
            },
            "scaffolding": self.scaffolding,
            "steps": [s.to_dict() for s in self.steps],
            "audit": self.audit.to_dict(),
        }
        if self.project_dir:
            d["project_dir"] = self.project_dir
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        task = d.get("task", {})
        return cls(
            id=d.get("id", ""),
            version=d.get("version", "1.0"),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            status=d.get("status", "planning"),
            parent_id=d.get("parent_id"),
            todo_id=d.get("todo_id"),
            title=task.get("title", ""),
            requirements=task.get("requirements", ""),
            tech_stack={
                str(k): str(v)
                for k, v in (task.get("tech_stack") or {}).items()
                if v
            },
            acceptance_criteria=task.get("acceptance_criteria", []),
            scaffolding=d.get("scaffolding", {}),
            steps=[PlanStep.from_dict(s) for s in d.get("steps", [])],
            audit=PlanAudit.from_dict(d.get("audit", {})),
            project_dir=d.get("project_dir", ""),
        )


# -------------------------------------------------------------------
# Heuristic dependency chain builder
# -------------------------------------------------------------------

_PHASE_KEYWORDS: list[tuple[int, set[str]]] = [
    # phase 0: scaffolding / init
    (0, {"scaffold", "scaffolding", "init", "initialize", "setup", "set up",
         "boilerplate", "project structure"}),
    # phase 1: data / schema / design
    (1, {"schema", "database", "db", "model", "data model", "design",
         "erd", "migration"}),
    # phase 2: core backend
    (2, {"backend", "server", "api", "fastapi", "express", "flask",
         "django", "endpoint", "core"}),
    # phase 2: core frontend (parallel with backend)
    (2, {"frontend", "react", "vue", "angular", "next", "svelte",
         "shell", "layout", "ui framework"}),
    # phase 3: services / integrations that need backend
    (3, {"integration", "service", "assemblyai", "openai", "anthropic",
         "claude", "transcription", "ai", "email", "resend", "stripe",
         "payment", "auth", "authentication", "websocket"}),
    # phase 4: feature UI that needs frontend + services
    (4, {"dashboard", "viewer", "upload", "interactive", "component",
         "page", "form", "widget", "chart", "ui feature"}),
    # phase 5: workflows / composition
    (5, {"workflow", "approval", "pipeline", "orchestrat", "report",
         "notification", "e2e", "end-to-end"}),
    # phase 6: testing
    (6, {"test", "testing", "spec", "e2e test", "integration test",
         "unit test", "qa"}),
    # phase 7: deploy / release
    (7, {"deploy", "deployment", "railway", "vercel", "heroku", "docker",
         "release", "publish", "ship", "ci/cd", "ci", "cd"}),
]


def _classify_step_phase(label: str) -> int:
    """Assign a heuristic phase number based on step label keywords.

    Picks the HIGHEST matching phase — if a step matches both
    "backend" (phase 2) and "deploy" (phase 7), it's a deploy step.
    """
    ll = label.lower()
    best_phase = -1
    for phase, keywords in _PHASE_KEYWORDS:
        for kw in keywords:
            if kw in ll:
                if phase > best_phase:
                    best_phase = phase
                break
    return best_phase if best_phase >= 0 else 3  # default: mid-tier


def _build_heuristic_dependency_chain(
    steps: list["PlanStep"],
) -> dict[str, list[str]] | None:
    """Build a dependency graph by classifying steps into phases.

    Steps in phase N depend on all steps in the previous phase(s).
    Returns {step_id: [dependency_labels]} or None if < 3 steps.
    """
    if len(steps) < 3:
        return None

    # Classify each step
    step_phases: list[tuple["PlanStep", int]] = [
        (s, _classify_step_phase(s.label)) for s in steps
    ]

    # Group by phase
    phase_groups: dict[int, list["PlanStep"]] = {}
    for s, phase in step_phases:
        phase_groups.setdefault(phase, []).append(s)

    sorted_phases = sorted(phase_groups.keys())
    if len(sorted_phases) < 2:
        # All steps classified into the same phase — can't build a chain
        return None

    # Build dependency map: each step depends on all steps from the
    # immediately preceding phase (not all earlier phases, to avoid
    # overly deep graphs that block parallelism).
    result: dict[str, list[str]] = {}
    for i, phase in enumerate(sorted_phases):
        if i == 0:
            for s in phase_groups[phase]:
                result[s.id] = []
        else:
            prev_phase = sorted_phases[i - 1]
            prev_labels = [s.label for s in phase_groups[prev_phase]]
            for s in phase_groups[phase]:
                result[s.id] = list(prev_labels)

    _wave_summary = " → ".join(
        f"{p}({len(phase_groups[p])})" for p in sorted_phases
    )
    logger.info(
        "PlanStore: heuristic chain phases: %s", _wave_summary,
    )
    return result


# -------------------------------------------------------------------
# Store
# -------------------------------------------------------------------


class PlanStore:
    """Manages plan JSON files in ``{workspace}/.plans/``."""

    PLANS_DIR = ".plans"

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)
        self._plans_dir = self._workspace / self.PLANS_DIR
        self._plans_dir.mkdir(parents=True, exist_ok=True)

    def _plan_path(self, plan_id: str) -> Path:
        return self._plans_dir / f"{plan_id}.json"

    # -- CRUD ----------------------------------------------------------

    def save(self, plan: Plan) -> Path:
        plan.touch()
        path = self._plan_path(plan.id)
        path.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def load(self, plan_id: str) -> Plan | None:
        path = self._plan_path(plan_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Plan.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def delete(self, plan_id: str) -> bool:
        path = self._plan_path(plan_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def archive(self, plan_id: str, reason: str = "") -> Plan | None:
        """Soft-delete: mark the plan as 'archived' so find_active() skips it."""
        plan = self.load(plan_id)
        if plan is None:
            return None
        plan.status = "archived"
        if reason:
            for s in plan.steps:
                if s.status in ("pending", "in_progress"):
                    s.status = "skipped"
                    s.notes = (s.notes + f" [skipped — plan archived: {reason}]").strip()
        plan.touch()
        self.save(plan)
        return plan

    def list_plans(self) -> list[Plan]:
        plans: list[Plan] = []
        for path in sorted(self._plans_dir.glob("plan_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                plans.append(Plan.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return plans

    # -- Discovery -----------------------------------------------------

    def find_active_roots(self) -> list[Plan]:
        """All non-archived root plans that count as still active."""
        import time as _time

        candidates = [
            p for p in self.list_plans()
            if p.parent_id is None
            and (
                p.status in ("planning", "in_progress", "blocked")
                or (
                    p.status == "failed"
                    and (_time.time() - p.updated_at) < 3600
                )
            )
        ]
        return sorted(candidates, key=lambda p: p.updated_at, reverse=True)

    def find_active(self) -> Plan | None:
        """Return the most recently updated non-done root plan.

        Also considers plans with ``"failed"`` status if they were
        updated within the last 60 minutes, guiding the agent to
        fix the existing plan rather than creating a second one.
        """
        roots = self.find_active_roots()
        if not roots:
            return None
        return roots[0]

    def find_recoverable(
        self,
        team_manager: Any | None = None,
        *,
        reopen: bool = True,
    ) -> Plan | None:
        """Most recent root plan that needs EM recovery (blocked, partial, false done)."""
        from nls.agentic.plan_work import find_recoverable_plan

        return find_recoverable_plan(self, team_manager, reopen=reopen)

    def resolve_work_plan(
        self,
        plan_id: str = "",
        team_manager: Any | None = None,
        *,
        reopen: bool = True,
    ) -> Plan | None:
        """Active plan, else recoverable plan; optional explicit plan_id."""
        from nls.agentic.plan_work import resolve_work_plan

        return resolve_work_plan(
            self, plan_id, team_manager, reopen=reopen,
        )

    def archive_sibling_active_roots(
        self, keep_plan_id: str, reason: str = "",
    ) -> list[str]:
        """Archive every other active root plan (one project = one active plan)."""
        archived: list[str] = []
        for plan in self.find_active_roots():
            if plan.id == keep_plan_id:
                continue
            self.archive(plan.id, reason or f"superseded by {keep_plan_id}")
            archived.append(plan.id)
        return archived

    def find_any_project_dir(self) -> str:
        """Return the project_dir from the most recent root plan that has one.

        Unlike ``find_active``, this searches ALL root plans regardless of
        status (including failed/partial/completed) so that follow-up plans
        reuse the same workspace folder instead of creating duplicates.
        """
        candidates = [
            p for p in self.list_plans()
            if p.parent_id is None and p.project_dir
        ]
        if not candidates:
            return ""
        best = max(candidates, key=lambda p: p.updated_at)
        return best.project_dir

    _SYSTEM_DIRS = frozenset({
        ".plans", ".nls_index", ".config", ".git", "node_modules",
        "uploads", "__pycache__", ".next", ".vscode",
    })

    def _detect_existing_project_dir(self) -> str:
        """Detect if a user-created project directory already exists.

        Scans the workspace root for non-system directories that look
        like a manually created project folder.  Returns the most
        recently modified one, or empty string if none found.
        """
        candidates: list[tuple[str, float]] = []
        try:
            for item in self._workspace.iterdir():
                if not item.is_dir():
                    continue
                name = item.name
                if name.startswith(".") or name in self._SYSTEM_DIRS:
                    continue
                has_files = any(item.iterdir())
                if has_files:
                    candidates.append((name, item.stat().st_mtime))
        except OSError:
            return ""
        if not candidates:
            return ""
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def find_by_todo(self, todo_id: str) -> Plan | None:
        for p in self.list_plans():
            if p.todo_id == todo_id:
                return p
        return None

    def find_by_id_prefix(self, prefix: str) -> Plan | None:
        """Find a plan whose ID starts with the given prefix."""
        for p in self.list_plans():
            if p.id.startswith(prefix):
                return p
        return None

    # -- Tree navigation -----------------------------------------------

    def get_children(self, plan_id: str) -> list[Plan]:
        return [p for p in self.list_plans() if p.parent_id == plan_id]

    def get_root(self, plan: Plan) -> Plan:
        """Walk up parent_id links to find the root plan."""
        current = plan
        seen: set[str] = {current.id}
        while current.parent_id:
            parent = self.load(current.parent_id)
            if parent is None or parent.id in seen:
                break
            seen.add(parent.id)
            current = parent
        return current

    def walk_tree(self, root_id: str) -> list[Plan]:
        """BFS traversal of the plan tree starting from root_id."""
        root = self.load(root_id)
        if root is None:
            return []
        result = [root]
        queue = [root_id]
        seen = {root_id}
        while queue:
            pid = queue.pop(0)
            for child in self.get_children(pid):
                if child.id not in seen:
                    seen.add(child.id)
                    result.append(child)
                    queue.append(child.id)
        return result

    # -- Plan creation helpers -----------------------------------------

    @staticmethod
    def _try_parse_json_array(s: str) -> list | None:
        """Try to parse a JSON array string, repairing truncated tails.

        LLMs sometimes emit steps as a JSON string but drop the closing
        bracket(s).  e.g. ``[{...}, {...}`` instead of ``[{...}, {...}]``.
        They also sometimes drop the closing ``}`` of the last dict,
        producing ``[..., {"key": "val"]`` or ``[..., "val"]]``.
        """
        s = s.strip()
        if not s.startswith("["):
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 1: append common missing closers
        for suffix in ("]", "}]", "\"}]", "\"}]}", "\"]}]"):
            try:
                parsed = json.loads(s + suffix)
                if isinstance(parsed, list) and len(parsed) > 0:
                    logger.warning(
                        "PlanStore: repaired truncated JSON array "
                        "(appended '%s', got %d items)",
                        suffix, len(parsed),
                    )
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue

        # Strategy 2: insert missing `}` between trailing `]` chars.
        # Handles: `[..., {"key": ["v"]]` → `[..., {"key": ["v"]}]`
        # The `}` could belong at any position in the bracket sequence.
        _trailing = s.rstrip()
        _trail_brackets = 0
        while _trailing.endswith("]"):
            _trailing = _trailing[:-1]
            _trail_brackets += 1
        if _trail_brackets >= 2:
            for pos in range(1, _trail_brackets + 1):
                for insert in ("}", "\"}", "\"}"):
                    candidate = (
                        _trailing
                        + "]" * pos
                        + insert
                        + "]" * (_trail_brackets - pos)
                    )
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list) and len(parsed) > 0:
                            logger.warning(
                                "PlanStore: repaired JSON array by inserting "
                                "'%s' at bracket pos %d/%d (got %d items)",
                                insert, pos, _trail_brackets, len(parsed),
                            )
                            return parsed
                    except (json.JSONDecodeError, ValueError):
                        continue

        return None

    @classmethod
    def _normalize_steps(cls, steps: Any) -> list:
        """Coerce steps into a proper list[dict], handling every LLM quirk.

        Known failure modes:
        - steps is a JSON string: '[{"label":...}, ...]'
        - steps is a truncated JSON string missing the closing ']'
        - steps is a list with ONE element that is itself a JSON string
          containing the real array
        - steps is double-encoded: '"[{\\"label\\":\\"...\\"}, ...]"'
        - steps is a list of dicts (normal case — pass through)
        """
        logger.debug(
            "PlanStore._normalize_steps: type=%s len=%s preview=%.200s",
            type(steps).__name__,
            len(steps) if hasattr(steps, "__len__") else "?",
            str(steps)[:200],
        )
        if isinstance(steps, str):
            cleaned = steps.strip()
            # Handle double-encoding: string starts with quote
            if cleaned.startswith('"') or cleaned.startswith("'"):
                try:
                    cleaned = json.loads(cleaned)
                    if isinstance(cleaned, str):
                        cleaned = cleaned.strip()
                except (json.JSONDecodeError, ValueError):
                    pass
            if isinstance(cleaned, str):
                parsed = cls._try_parse_json_array(cleaned)
                if parsed is not None:
                    steps = parsed
                else:
                    # Maybe it's a single dict
                    try:
                        obj = json.loads(cleaned)
                        if isinstance(obj, dict):
                            steps = [obj]
                        else:
                            steps = [{"label": str(steps)}]
                    except (json.JSONDecodeError, ValueError):
                        logger.warning(
                            "PlanStore: steps is unparseable string "
                            "(len=%d): %.200s",
                            len(steps), steps,
                        )
                        steps = [{"label": steps}]
            elif isinstance(cleaned, list):
                steps = cleaned
            else:
                steps = [{"label": str(steps)}]

        if not isinstance(steps, list):
            steps = [{"label": str(steps)}]

        # If list has exactly 1 element that is a string containing a
        # JSON array, the whole array was stuffed into one slot.
        if (
            len(steps) == 1
            and isinstance(steps[0], str)
            and steps[0].strip().startswith("[")
        ):
            parsed = cls._try_parse_json_array(steps[0])
            if parsed is not None and len(parsed) > 1:
                logger.info(
                    "PlanStore: unwrapped single-string step → %d real steps",
                    len(parsed),
                )
                steps = parsed

        # Same for dict: if 1 element is a dict whose first value looks
        # like it contains sub-steps serialized as JSON.
        if (
            len(steps) == 1
            and isinstance(steps[0], dict)
            and "label" in steps[0]
            and isinstance(steps[0]["label"], str)
            and steps[0]["label"].strip().startswith("[")
        ):
            parsed = cls._try_parse_json_array(steps[0]["label"])
            if parsed is not None and len(parsed) > 1:
                logger.info(
                    "PlanStore: unwrapped JSON-in-label → %d real steps",
                    len(parsed),
                )
                steps = parsed

        # Nuclear fallback: if we still have 1 step whose label contains
        # multiple JSON-like objects, extract them individually via regex.
        if len(steps) == 1:
            _raw = ""
            if isinstance(steps[0], str):
                _raw = steps[0]
            elif isinstance(steps[0], dict):
                _raw = steps[0].get("label", "")
            if isinstance(_raw, str) and _raw.count('"label"') >= 2:
                extracted = cls._extract_step_objects(_raw)
                if extracted and len(extracted) > 1:
                    logger.warning(
                        "PlanStore: regex extraction recovered %d steps "
                        "from unparseable string (len=%d)",
                        len(extracted), len(_raw),
                    )
                    steps = extracted

        return steps

    @staticmethod
    def _extract_step_objects(raw: str) -> list[dict] | None:
        """Last-resort extraction of step dicts from malformed JSON.

        Finds individual ``{...}`` blocks that look like step objects
        and parses each independently. Handles cases where the overall
        array JSON is broken but individual objects are intact.
        """
        results: list[dict] = []
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    fragment = raw[start : i + 1]
                    try:
                        obj = json.loads(fragment)
                        if isinstance(obj, dict) and ("label" in obj or "description" in obj):
                            results.append(obj)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    start = -1
        # If the last object was missing its closing }, try to repair it
        if depth > 0 and start >= 0:
            fragment = raw[start:]
            for suffix in ("}", '"}', '"]}'):
                try:
                    obj = json.loads(fragment + suffix)
                    if isinstance(obj, dict) and ("label" in obj or "description" in obj):
                        results.append(obj)
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
        return results if results else None

    def create_plan(
        self,
        title: str,
        requirements: str = "",
        tech_stack: dict[str, str] | None = None,
        acceptance_criteria: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        scaffolding: dict[str, dict[str, str]] | None = None,
        todo_id: str | None = None,
        parent_id: str | None = None,
        project_dir: str | None = None,
        orchestration_profile: str = "solo_structured",
    ) -> Plan:
        # Defensive: LLMs may pass list args as JSON strings
        if isinstance(acceptance_criteria, str):
            try:
                acceptance_criteria = json.loads(acceptance_criteria)
            except (json.JSONDecodeError, ValueError):
                acceptance_criteria = [acceptance_criteria]
            if not isinstance(acceptance_criteria, list):
                acceptance_criteria = [str(acceptance_criteria)]

        # Auto-generate project directory from title (top-level plans only).
        # Sub-plans inherit the parent's project_dir.
        # CRITICAL: Search ALL root plans (including failed/partial/completed)
        # so follow-up plans reuse the same workspace folder.
        _project_dir = project_dir or ""
        if not _project_dir and not parent_id:
            _project_dir = self.find_any_project_dir()
            if not _project_dir:
                # Detect if the orchestrator already created a project
                # folder manually (via bash mkdir) before creating the plan.
                _project_dir = self._detect_existing_project_dir()
            if not _project_dir:
                _project_dir = _slugify(title)
            (self._workspace / _project_dir).mkdir(parents=True, exist_ok=True)
        elif not _project_dir and parent_id:
            parent = self.load(parent_id)
            if parent and parent.project_dir:
                _project_dir = parent.project_dir

        _stack: dict[str, str] = {}
        if tech_stack:
            _stack = {str(k): str(v) for k, v in tech_stack.items() if v}

        plan = Plan(
            title=title,
            requirements=requirements,
            tech_stack=_stack,
            acceptance_criteria=acceptance_criteria or [],
            scaffolding=scaffolding or {},
            todo_id=todo_id,
            parent_id=parent_id,
            status="planning",
            project_dir=_project_dir,
        )
        if steps:
            steps = self._normalize_steps(steps)

            for idx, s in enumerate(steps, start=1):
                step_id = f"step-{idx}"
                if isinstance(s, str):
                    plan.steps.append(PlanStep(id=step_id, label=s))
                elif isinstance(s, dict):
                    _label = (
                        s.get("label")
                        or s.get("step")
                        or s.get("title")
                        or s.get("name")
                        or s.get("description")
                        or ""
                    )
                    plan.steps.append(PlanStep(
                        id=step_id,
                        label=_label,
                        description=s.get("description") or "",
                        output_files=s.get("output_files", []),
                        owned_paths=s.get("owned_paths") or [],
                        depends_on=s.get("depends_on") or [],
                        delegatable=bool(
                            s.get("delegatable")
                            if "delegatable" in s
                            else plan_step_delegatable_default(orchestration_profile)
                        ),
                    ))
                else:
                    plan.steps.append(PlanStep(id=step_id, label=str(s)))
        # Auto-mark delegatable only for full orchestration profiles.
        if should_auto_mark_delegatable(orchestration_profile, len(plan.steps)):
            if not any(s.delegatable for s in plan.steps):
                for s in plan.steps:
                    s.delegatable = True
                logger.info(
                    "PlanStore: auto-marked %d steps as delegatable "
                    "(orchestrated profile)",
                    len(plan.steps),
                )

        if self.apply_solo_step_policy(plan, orchestration_profile):
            logger.info(
                "PlanStore: forced non-delegatable steps for solo_structured profile",
            )

        self.ensure_dependency_safety_net(plan)

        from nls.agentic.wave_coordination import normalize_plan_step_paths
        normalize_plan_step_paths(plan)

        self.save(plan)
        return plan

    @staticmethod
    def apply_solo_step_policy(plan: Plan, orchestration_profile: str | None) -> bool:
        """Force non-delegatable steps in solo execution mode. Returns True if changed."""
        if not is_solo_execution_profile(orchestration_profile):
            return False
        changed = False
        for step in plan.steps:
            if step.delegatable:
                step.delegatable = False
                changed = True
        return changed

    def ensure_dependency_safety_net(self, plan: Plan) -> int:
        """Fill missing depends_on when the graph is empty or has orphans.

        Safe to call after LLM dependency inference — re-applies heuristics
        if inference wiped or flattened the graph.
        """
        patched = 0
        if len(plan.steps) < 2:
            return patched

        first = plan.steps[0]
        delegatable_steps = [s for s in plan.steps if s.delegatable]
        has_any_deps = any(s.depends_on for s in plan.steps[1:])
        if has_any_deps:
            for s in plan.steps[1:]:
                if s.delegatable and not s.depends_on:
                    s.depends_on = [first.label]
                    patched += 1
                    logger.info(
                        "PlanStore: auto-injected depends_on=[%s] "
                        "for orphan step '%s'",
                        first.label, s.label,
                    )
        elif len(delegatable_steps) >= 2 and first.delegatable:
            _chain = _build_heuristic_dependency_chain(plan.steps)
            if _chain:
                for s in plan.steps:
                    if s.id in _chain and _chain[s.id]:
                        if s.depends_on != _chain[s.id]:
                            s.depends_on = _chain[s.id]
                            patched += 1
                if patched:
                    logger.info(
                        "PlanStore: heuristic chain patched %d steps "
                        "(no deps existed in plan)",
                        patched,
                    )
            else:
                for s in delegatable_steps[1:]:
                    if not s.depends_on:
                        s.depends_on = [first.label]
                        patched += 1
                        logger.info(
                            "PlanStore: flat fallback depends_on=[%s] "
                            "for step '%s'",
                            first.label, s.label,
                        )
        return patched

    def create_sub_plan(
        self,
        parent_plan_id: str,
        parent_step_id: str,
        title: str,
        requirements: str = "",
        tech_stack: dict[str, str] | None = None,
        acceptance_criteria: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        scaffolding: dict[str, dict[str, str]] | None = None,
        orchestration_profile: str = "solo_structured",
    ) -> Plan | None:
        parent = self.load(parent_plan_id)
        if parent is None:
            return None
        step = parent.get_step(parent_step_id)
        if step is None:
            return None

        sub = self.create_plan(
            title=title,
            requirements=requirements or parent.requirements,
            tech_stack=(
                {str(k): str(v) for k, v in tech_stack.items() if v}
                if tech_stack
                else dict(parent.tech_stack) if parent.tech_stack else None
            ),
            acceptance_criteria=acceptance_criteria,
            steps=steps,
            scaffolding=scaffolding,
            parent_id=parent_plan_id,
            orchestration_profile=orchestration_profile,
        )

        step.sub_plan_id = sub.id
        self.save(parent)
        return sub


# -------------------------------------------------------------------
# v4 dependency graph helpers
# -------------------------------------------------------------------


def get_dependency_graph(plan: Plan) -> dict[str, list[str]]:
    """Return adjacency list: step_id → resolved dependency step IDs."""
    step_map = {s.id: s for s in plan.steps}
    label_map = {s.label.lower().strip(): s.id for s in plan.steps}
    graph: dict[str, list[str]] = {}
    for step in plan.steps:
        resolved: list[str] = []
        for dep in step.depends_on:
            dep_id = _resolve_dep_id(dep, step_map, label_map)
            if dep_id in step_map and dep_id != step.id:
                resolved.append(dep_id)
        graph[step.id] = resolved
    return graph


def detect_dependency_cycles(plan: Plan) -> list[list[str]]:
    """Return dependency cycles as lists of step IDs (may be empty)."""
    graph = get_dependency_graph(plan)
    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        if node in stack:
            if node in path:
                start = path.index(node)
                cycle = path[start:] + [node]
                if len(cycle) > 2:
                    cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        path.append(node)
        for dep in graph.get(node, []):
            dfs(dep)
        path.pop()
        stack.discard(node)

    for step_id in graph:
        dfs(step_id)

    # Deduplicate rotated cycles
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cycle in cycles:
        core = tuple(cycle[:-1]) if cycle and cycle[0] == cycle[-1] else tuple(cycle)
        if len(core) < 2:
            continue
        key = tuple(sorted(core))
        if key not in seen:
            seen.add(key)
            unique.append(list(core))
    return unique


_SERVICE_MODULE_KW = (
    "service", "transcription", "assemblyai", "anthropic",
    "analysis", "email", "nodemailer",
)
_API_LAYER_KW = (
    "api", "endpoint", "rest api", "express", "fastapi",
    "backend api", "routes",
)


def break_service_before_api_edges(plan: Plan) -> int:
    """Drop edges where an internal service module depends on the API layer."""
    step_map = {s.id: s for s in plan.steps}
    label_map = {s.label.lower().strip(): s.id for s in plan.steps}
    patched = 0

    def _step_for_ref(ref: str) -> PlanStep | None:
        sid = _resolve_dep_id(ref, step_map, label_map)
        return step_map.get(sid)

    for step in plan.steps:
        ll = step.label.lower()
        if any(k in ll for k in _API_LAYER_KW):
            continue
        if not any(k in ll for k in _SERVICE_MODULE_KW):
            continue
        kept: list[str] = []
        for dep in step.depends_on:
            dep_step = _step_for_ref(dep)
            if dep_step is None:
                kept.append(dep)
                continue
            dep_ll = dep_step.label.lower()
            if any(k in dep_ll for k in _API_LAYER_KW):
                patched += 1
                continue
            kept.append(dep)
        if kept != step.depends_on:
            step.depends_on = kept
    return patched


def format_dependency_cycle_hints(plan: Plan) -> str:
    """Human-readable fix guidance when the graph has cycles."""
    cycles = detect_dependency_cycles(plan)
    if not cycles:
        return ""

    lines = ["Dependency cycles detected:"]
    step_map = {s.id: s for s in plan.steps}
    for cycle in cycles[:3]:
        labels = [
            f"{sid} ({step_map[sid].label})" if sid in step_map else sid
            for sid in cycle
        ]
        lines.append(f"  • {' → '.join(labels)}")
    lines.append(
        "Fix: plan(action='fix_dependencies') or plan(action='update', "
        "step_id='...', depends_on=[...]). Internal service modules "
        "(AssemblyAI, Anthropic, email) should depend on schema/DB only — "
        "NOT on the HTTP API layer. The API step should depend on services."
    )
    lines.append(
        "Do NOT plan(delete) while completed steps exist — that discards "
        "progress. Use plan(continue_work) only if you truly need a new plan."
    )
    return "\n".join(lines)


def format_unmet_dependency_hints(
    plan: Plan,
    unmet: list[tuple[PlanStep, PlanStep]],
) -> str:
    """Concrete hints after team(launch) blocked on prerequisites."""
    if not unmet:
        return ""

    lines = [
        "Suggested fixes:",
    ]
    for step, dep in unmet[:6]:
        dep_ll = dep.label.lower()
        step_ll = step.label.lower()
        if any(k in step_ll for k in _SERVICE_MODULE_KW) and any(
            k in dep_ll for k in _API_LAYER_KW
        ):
            lines.append(
                f"  • Remove \"{dep.label}\" from {step.id} depends_on — "
                f"service modules are imported by the API, not the reverse."
            )
        else:
            lines.append(
                f"  • Mark {dep.id} done (or accept_partial) before launching "
                f"\"{step.label}\", or remove that dependency if work already "
                f"exists on disk."
            )
    cycle_hint = format_dependency_cycle_hints(plan)
    if cycle_hint:
        lines.append(cycle_hint)
    lines.append(
        "Or run plan(action='fix_dependencies', plan_id='"
        f"{plan.id}') to auto-repair the graph."
    )
    return "\n".join(lines)


def _resolve_dep_id(dep: str, step_map: dict[str, Any], label_map: dict[str, str]) -> str:
    """Resolve a dependency reference to a step ID.

    LLMs sometimes put step labels (or partial labels) in depends_on
    instead of IDs.  Tries exact match, then case-insensitive, then
    prefix/substring match against known labels.
    """
    if dep in step_map:
        return dep
    needle = dep.lower().strip()
    if needle in label_map:
        return label_map[needle]
    for label, sid in label_map.items():
        if label.startswith(needle) or needle.startswith(label):
            return sid
    return dep


def _add_implicit_dep(step: PlanStep, dep_id: str, step_map: dict[str, PlanStep]) -> None:
    """Append a dependency by step id if both steps exist and not already listed."""
    if dep_id not in step_map or dep_id == step.id:
        return
    if dep_id in step.depends_on:
        return
    dep_label = step_map[dep_id].label
    if dep_label in step.depends_on:
        return
    step.depends_on = list(step.depends_on) + [dep_label]


def _infer_implicit_dependencies(plan: Plan) -> None:
    """Add common build-order edges missing from LLM-authored depends_on lists."""
    step_map = {s.id: s for s in plan.steps}

    def _find(*needles: str) -> PlanStep | None:
        for s in plan.steps:
            lbl = s.label.lower()
            if all(n in lbl for n in needles):
                return s
        return None

    models = _find("backend", "data model") or _find("data model", "database")
    api = _find("backend", "api") or _find("api endpoint")
    fe_int = _find("integrate", "frontend") or _find("frontend", "backend api")
    transcription = _find("assembly", "transcription") or _find("transcription", "service")
    analysis = _find("anthropic", "analysis") or _find("analysis", "service")

    if models and api:
        _add_implicit_dep(api, models.id, step_map)
    if api and fe_int:
        _add_implicit_dep(fe_int, api.id, step_map)
        if models:
            _add_implicit_dep(fe_int, models.id, step_map)
    if models and transcription:
        _add_implicit_dep(transcription, models.id, step_map)
    if models and analysis:
        _add_implicit_dep(analysis, models.id, step_map)
    if api and analysis:
        _add_implicit_dep(analysis, api.id, step_map)


def get_delegation_waves(plan: Plan) -> list[list[PlanStep]]:
    """Pure topological sort of steps by depends_on into execution waves.

    Wave 0: steps with no dependencies.
    Wave 1: steps whose dependencies are all in wave 0.
    Etc.  Delegatable steps are preferred within each wave.

    IMPORTANT: Wave assignment is based ONLY on the dependency graph
    structure, NOT on actual step completion status.  This keeps wave
    numbering stable so ``_try_create_next_wave`` can match the wave
    index used when the team was originally created.
    """
    _infer_implicit_dependencies(plan)
    step_map = {s.id: s for s in plan.steps}
    label_map = {s.label.lower().strip(): s.id for s in plan.steps}
    placed_ids: set[str] = set()
    waves: list[list[PlanStep]] = []

    remaining = list(plan.steps)
    while remaining:
        wave: list[PlanStep] = []
        for step in remaining:
            resolved_deps = {
                _resolve_dep_id(d, step_map, label_map) for d in step.depends_on
            }
            unmet = resolved_deps - placed_ids
            if not unmet:
                wave.append(step)
        if not wave:
            wave = remaining[:1]
        wave.sort(key=lambda s: (not s.delegatable, s.id))
        waves.append(wave)
        wave_ids = {s.id for s in wave}
        placed_ids |= wave_ids
        remaining = [s for s in remaining if s.id not in wave_ids]

    return waves
