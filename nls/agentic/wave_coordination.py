"""Wave launch helpers — tech stack lock-in and per-delegate file ownership."""

from __future__ import annotations

import json
from typing import Any, Callable

# Fallback when project root cannot be resolved (no hardcoded monorepo paths).
_DEFAULT_SHARED_PATHS: frozenset[str] = frozenset()

# Canonical tech_stack keys the orchestrator should set on plan(create).
TECH_STACK_KEYS: tuple[str, ...] = (
    "backend_language",
    "backend_framework",
    "frontend_framework",
    "database",
    "orm",
    "package_manager",
    "deploy_target",
)


def format_structured_tech_stack(tech_stack: dict[str, str] | None) -> str:
    """Render plan.tech_stack dict as a compact mandatory block."""
    if not tech_stack:
        return ""
    lines = ["Structured stack (authoritative — do not drift):"]
    for key in TECH_STACK_KEYS:
        val = (tech_stack.get(key) or "").strip()
        if val:
            lines.append(f"  - {key}: {val}")
    for key, val in tech_stack.items():
        if key in TECH_STACK_KEYS or not val:
            continue
        lines.append(f"  - {key}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_tech_stack_block(
    requirements: str = "",
    *,
    plan_title: str = "",
    tech_stack: dict[str, str] | None = None,
    plan: Any | None = None,
) -> str:
    """Build mandatory tech-stack context for delegate/orchestrator prompts."""
    if plan is not None:
        requirements = getattr(plan, "requirements", "") or requirements
        plan_title = getattr(plan, "title", "") or plan_title
        tech_stack = getattr(plan, "tech_stack", None) or tech_stack

    req = (requirements or "").strip()
    stack = tech_stack or {}
    structured = format_structured_tech_stack(stack)

    if not req and not structured:
        return ""

    lines: list[str] = [
        "[TECH STACK — MANDATORY — do not drift]",
        "Use ONLY the stack declared below. Do not substitute frameworks "
        "(e.g. do not build FastAPI if the plan says Express).",
        "Match languages, package managers, and ORM choices to the spec.",
    ]
    if plan_title:
        lines.append(f"Plan: {plan_title[:200]}")
    if structured:
        lines.append("")
        lines.append(structured)
    if req:
        lines.append("")
        lines.append("Full requirements (authoritative):")
        chunk = req[:2500]
        if len(req) > 2500:
            chunk += "\n...(truncated — use plan(action='read') for full text)"
        lines.append(chunk)
    return "\n".join(lines)


# Valid first-segment names inside project_dir (not the project folder itself).
_INNER_PROJECT_ROOTS: frozenset[str] = frozenset({
    "backend", "frontend", "server", "client", "src", "app", "packages",
    "migrations", "db", "prisma", "tests", "test", "scripts", "docs",
    "infra", "deploy", "docker", "public", "lib", "shared", "api",
    "internal", "cmd", "pkg", "web", "mobile", "services", "components",
})

_ROOT_FILE_NAMES: frozenset[str] = frozenset({
    "README.md", "package.json", "pnpm-workspace.yaml", "pyproject.toml",
    "requirements.txt", "Dockerfile", "docker-compose.yml", "Makefile",
    "railway.json", "railway.toml", ".gitignore", ".env.example",
})


def _clean_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/").lstrip("/")


def _is_root_file_segment(segment: str) -> bool:
    seg = (segment or "").strip()
    if not seg:
        return False
    if seg.startswith("."):
        return True
    return seg in _ROOT_FILE_NAMES


def _is_valid_inner_root(segment: str) -> bool:
    return (segment or "").lower() in _INNER_PROJECT_ROOTS


def normalize_project_relative_path(path: str, project_dir: str = "") -> str:
    """Strip project_dir and spurious nested project-folder prefixes."""
    raw = (path or "").strip().replace("\\", "/")
    trailing_slash = raw.endswith("/")
    p = _clean_path(raw)
    if not p:
        return ""

    pd = _clean_path(project_dir).strip("/")
    if pd:
        if p == pd:
            return ""
        if p.startswith(f"{pd}/"):
            p = p[len(pd) + 1:]

    parts = [x for x in p.split("/") if x]
    if len(parts) >= 2:
        first = parts[0]
        if (
            not _is_valid_inner_root(first)
            and not _is_root_file_segment(first)
            and (not pd or first != pd)
        ):
            p = "/".join(parts[1:])

    if trailing_slash and p and not p.endswith("/"):
        p += "/"
    return p


def normalize_path_list(paths: list[str], project_dir: str = "") -> list[str]:
    """Normalize and dedupe path patterns relative to project_dir."""
    out: list[str] = []
    for raw in paths or []:
        norm = normalize_project_relative_path(raw, project_dir)
        if norm and norm not in out:
            out.append(norm)
    return out


def validate_step_paths_for_project(
    project_dir: str,
    paths: list[str],
    *,
    step_id: str = "",
) -> list[str]:
    """Return warnings when paths include project_dir or a fictional subfolder."""
    warnings: list[str] = []
    pd = _clean_path(project_dir).strip("/")
    prefix = f"Step {step_id}: " if step_id else ""
    for raw in paths or []:
        p = _clean_path(raw)
        if not p:
            continue
        if pd and (p == pd or p.startswith(f"{pd}/")):
            warnings.append(
                f"{prefix}'{raw}' includes project_dir '{pd}/' — use paths "
                f"relative to the project root (e.g. backend/, .gitignore)."
            )
            continue
        parts = [x for x in p.split("/") if x]
        if len(parts) < 2:
            continue
        first = parts[0]
        if (
            first != pd
            and not _is_valid_inner_root(first)
            and not _is_root_file_segment(first)
        ):
            inner = "/".join(parts[1:])
            warnings.append(
                f"{prefix}'{raw}' starts with '{first}/' but project_dir is "
                f"'{pd or '(unset)'}' — use project-relative paths "
                f"(e.g. {inner or 'backend/'})."
            )
    return warnings


def validate_plan_step_paths(plan: Any) -> list[str]:
    """Collect path alignment warnings for all steps on a plan."""
    pd = getattr(plan, "project_dir", "") or ""
    warnings: list[str] = []
    for step in getattr(plan, "steps", []) or []:
        sid = getattr(step, "id", "") or ""
        warnings.extend(
            validate_step_paths_for_project(
                pd, getattr(step, "owned_paths", None) or [], step_id=sid,
            )
        )
        warnings.extend(
            validate_step_paths_for_project(
                pd, getattr(step, "output_files", None) or [], step_id=sid,
            )
        )
    return warnings


def normalize_plan_step_paths(plan: Any) -> int:
    """Rewrite step owned_paths and output_files relative to project_dir."""
    pd = getattr(plan, "project_dir", "") or ""
    changed = 0
    for step in getattr(plan, "steps", []) or []:
        owned = normalize_path_list(getattr(step, "owned_paths", None) or [], pd)
        outputs = normalize_path_list(getattr(step, "output_files", None) or [], pd)
        if owned != (getattr(step, "owned_paths", None) or []):
            step.owned_paths = owned
            changed += 1
        if outputs != (getattr(step, "output_files", None) or []):
            step.output_files = outputs
            changed += 1
    return changed


def resolve_step_owned_paths(
    step: Any | None,
    project_dir: str = "",
) -> list[str]:
    """Path patterns for a delegate — explicit plan step fields only."""
    patterns: list[str] = []

    def _add(p: str) -> None:
        p = normalize_project_relative_path(p, project_dir)
        if p and p not in patterns:
            patterns.append(p)

    if step is not None:
        for p in getattr(step, "owned_paths", None) or []:
            _add(p)
        for p in getattr(step, "output_files", None) or []:
            _add(p)

    return patterns


def derive_shared_paths(project_root: "Path") -> list[str]:
    """Project-relative integration files parallel delegates must not edit."""
    from pathlib import Path

    root = Path(project_root)
    if not root.is_dir():
        return []

    shared: list[str] = []
    for name in ("package.json", "pnpm-workspace.yaml", "pyproject.toml", "README.md"):
        if (root / name).is_file():
            shared.append(name)

    for init in root.rglob("services/__init__.py"):
        if ".git" in init.parts:
            continue
        rel = init.relative_to(root).as_posix()
        if rel.count("/") <= 5:
            shared.append(rel)

    for pattern in (
        "packages/server/app/main.py",
        "packages/server/src/index.ts",
        "packages/server/src/app.ts",
        "packages/client/src/App.tsx",
        "packages/client/src/main.tsx",
        "src/App.tsx",
        "app/main.py",
        "main.py",
        "src/index.ts",
        "src/main.tsx",
    ):
        if (root / pattern).is_file() and pattern not in shared:
            shared.append(pattern)

    return shared[:12]


def build_file_ownership_block(
    *,
    delegate_number: int,
    owned_patterns: list[str],
    peer_lines: list[str],
    shared_paths: list[str] | None = None,
) -> str:
    """Per-delegate file ownership section for task preamble."""
    lines = [
        "[FILE OWNERSHIP — this wave]",
        f"You are delegate #{delegate_number}. Primary work goes under YOUR paths.",
        "Shared integration files are orchestrator-only unless listed in your "
        "owned_paths (co-ownership — add the path on both steps if two delegates "
        "need the same file).",
        "Scratch files are OK outside exclusive teammate scopes (tmp_*.json, temp/).",
    ]
    if owned_patterns:
        lines.append("\nYour paths (exclusive):")
        for p in owned_patterns[:12]:
            lines.append(f"  - {p}")
    if peer_lines:
        lines.append("\nTeammates (do not edit their files):")
        lines.extend(peer_lines[:8])
    shared = list(shared_paths) if shared_paths is not None else list(_DEFAULT_SHARED_PATHS)
    if shared:
        lines.append("\nShared (orchestrator merges only — do not write):")
        for p in shared[:8]:
            lines.append(f"  - {p}")
    return "\n".join(lines)


def build_wave_ownership_registry(
    members: list[Any],
    plan: Any | None = None,
) -> tuple[dict[int, list[str]], list[str]]:
    """Map delegate_number -> path patterns for ledger enforcement."""
    registry: dict[int, list[str]] = {}
    peer_summaries: list[str] = []
    for m in members:
        num = getattr(m, "delegate_number", None)
        label = getattr(m, "task", "") or ""
        headline = label.split("\n")[0][:120]
        step = None
        if plan is not None and getattr(m, "step_id", ""):
            get_step = getattr(plan, "get_step", None)
            if get_step:
                step = get_step(getattr(m, "step_id", ""))
        patterns = resolve_step_owned_paths(
            step,
            getattr(plan, "project_dir", "") or "" if plan is not None else "",
        )
        if num is not None and num >= 0:
            registry[num] = patterns
            headline_short = label.split("\n")[0][:80]
            scope_hint = ", ".join(patterns[:3]) if patterns else (
                "NO owned_paths — set on plan step before launch"
            )
            peer_summaries.append(
                f"  - #{num}: {headline_short} → {scope_hint}"
            )
    return registry, peer_summaries


def _read_json_deps(path: "Path") -> dict[str, Any]:
    try:
        import json as _json
        return _json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _iter_package_json_files(root: "Path") -> list["Path"]:
    from pathlib import Path

    found: list[Path] = []
    for pkg in root.rglob("package.json"):
        if ".git" in pkg.parts or "node_modules" in pkg.parts:
            continue
        found.append(pkg)
    return found


def _npm_dep_present(root: "Path", dep: str) -> bool:
    dep = dep.lower()
    for pkg in _iter_package_json_files(root):
        data = _read_json_deps(pkg)
        all_deps = {
            **(data.get("dependencies") or {}),
            **(data.get("devDependencies") or {}),
        }
        if dep in {k.lower() for k in all_deps}:
            return True
    return False


def _python_dep_present(root: "Path", pkg_name: str) -> bool:
    needle = pkg_name.lower()
    for name in ("requirements.txt", "pyproject.toml", "Pipfile"):
        path = root / name
        if not path.is_file():
            continue
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace").lower():
                return True
        except Exception:
            pass
    return False


def _file_named_exists(root: "Path", filename: str) -> bool:
    from pathlib import Path

    for path in root.rglob(filename):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        if path.name == filename:
            return True
    return False


# Declarative markers keyed by tokens in plan.tech_stack values.
_STACK_MARKERS: dict[str, tuple[str, Callable[["Path"], bool]]] = {
    "express": ("express npm dependency", lambda r: _npm_dep_present(r, "express")),
    "fastapi": ("fastapi in Python deps", lambda r: _python_dep_present(r, "fastapi")),
    "django": ("django in Python deps", lambda r: _python_dep_present(r, "django")),
    "react": ("react npm dependency", lambda r: _npm_dep_present(r, "react")),
    "next.js": ("next npm dependency", lambda r: _npm_dep_present(r, "next")),
    "nextjs": ("next npm dependency", lambda r: _npm_dep_present(r, "next")),
    "prisma": ("schema.prisma file", lambda r: _file_named_exists(r, "schema.prisma")),
    "vite": ("vite npm dependency", lambda r: _npm_dep_present(r, "vite")),
}


def _declared_stack_tokens(tech_stack: dict[str, str]) -> set[str]:
    tokens: set[str] = set()
    for val in tech_stack.values():
        text = (val or "").strip().lower()
        if not text:
            continue
        for marker in _STACK_MARKERS:
            if marker in text:
                tokens.add(marker)
    return tokens


def detect_tech_stack_drift(
    requirements: str,
    project_root: str,
    *,
    tech_stack: dict[str, str] | None = None,
) -> list[str]:
    """Verify project markers match structured plan.tech_stack only."""
    from pathlib import Path

    _ = requirements  # requirements text is not used for drift heuristics
    issues: list[str] = []
    root = Path(project_root)
    if not root.is_dir():
        return issues

    stack = {str(k): str(v).strip() for k, v in (tech_stack or {}).items() if v}
    if not stack:
        return [
            "No structured tech_stack on plan — call plan(action='set_tech_stack') "
            "before verify/complete."
        ]

    declared = _declared_stack_tokens(stack)
    if not declared:
        return [
            "tech_stack is set but no known markers matched — use values like "
            "express, fastapi, react, prisma in backend_framework/orm/frontend_framework."
        ]

    detected = {token for token, (_, check) in _STACK_MARKERS.items() if check(root)}

    for token in sorted(declared):
        desc, check = _STACK_MARKERS[token]
        if not check(root):
            issues.append(
                f"Stack compliance: plan declares {token} but {desc} not found on disk"
            )

    undeclared = detected - declared
    if undeclared:
        issues.append(
            "Stack drift: undeclared components detected on disk: "
            + ", ".join(sorted(undeclared))
        )

    return issues


def normalize_tech_stack_param(raw: Any) -> dict[str, str]:
    """Parse tech_stack from tool params (dict or JSON string)."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).strip() for k, v in raw.items() if v}
