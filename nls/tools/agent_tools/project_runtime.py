"""Shared project-root and virtual-environment helpers for agent tools.

Used by ``bash`` (PATH / project ``python``) and ``project_install`` so both
target the same project-local Python environment.
"""

from __future__ import annotations

import logging
import re
import sys
import venv as _venv_mod
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

PROJECT_MARKERS = (
    ".git",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "setup.py",
    "pom.xml",
)

_PYTHON_MARKERS = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "Pipfile",
)

_NODE_MARKERS = (
    "package.json",
)

_PYPI_PACKAGE_TOKEN_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]*$",
)

# Tokens that are virtually always PyPI, not npm package names.
_PYPI_STRONG_NAMES = frozenset({
    "sqlalchemy", "psycopg2-binary", "psycopg2", "asyncpg", "fastapi",
    "uvicorn", "alembic", "pydantic", "django", "flask", "requests",
    "numpy", "pandas", "pytest", "httpx", "starlette", "python-dotenv",
})

# Directories skipped when discovering package.json / requirements under a tree.
_DEP_INSTALL_IGNORE_DIRS = frozenset({
    "node_modules",
    ".venv",
    "venv",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".turbo",
    ".next",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
})


def split_pip_package_args(package: str) -> list[str]:
    """Split a pip install spec into separate package arguments."""
    return [p for p in (package or "").split() if p.strip()]


def looks_like_pypi_package_spec(package: str) -> bool:
    """True when *package* should use pip, not npm (avoids EINVALIDTAGNAME)."""
    text = (package or "").strip()
    if not text:
        return False
    if text.startswith(("-r", "--requirement")):
        return True
    parts = split_pip_package_args(text)
    if not parts:
        return False
    if len(parts) > 1:
        return all(_PYPI_PACKAGE_TOKEN_RE.match(p) for p in parts)
    token = parts[0]
    if token.startswith("@") and "/" in token:
        return False
    if any(ch.isupper() for ch in token):
        return True
    base = (
        token.split(">=")[0].split("<=")[0].split("==")[0].split("[")[0].strip().lower()
    )
    if base in _PYPI_STRONG_NAMES or base.startswith("psycopg"):
        return True
    return False


def cwd_has_python_work(effective_cwd: Path, stop: Path) -> bool:
    """True when the delegate is working in a Python tree (backend, etc.)."""
    current = effective_cwd.resolve()
    for _ in range(6):
        if list(current.glob("*.py"))[:1]:
            return True
        if (current / "requirements.txt").is_file():
            return True
        if any((current / m).is_file() for m in _PYTHON_MARKERS):
            return True
        if current == stop or current.parent == current:
            break
        try:
            current.relative_to(stop)
        except ValueError:
            break
        current = current.parent
    return False


def plan_install_boundary(
    workspace_root: str,
    plan_project_dir: str | None = None,
) -> Path:
    """Upper bound for walking up from CWD (plan folder or workspace)."""
    workspace = Path(workspace_root).resolve()
    plan_dir = (plan_project_dir or "").strip().strip("/\\")
    if plan_dir:
        planned = (workspace / plan_dir).resolve()
        try:
            planned.relative_to(workspace)
            return planned
        except ValueError:
            pass
    return workspace


def walk_up_find_file(
    start: Path,
    stop: Path,
    filename: str,
) -> Path | None:
    """Return the first *filename* found walking up from *start* (inclusive)."""
    current = start.resolve()
    stop = stop.resolve()
    while True:
        candidate = current / filename
        if candidate.is_file():
            return candidate
        if current == stop or current.parent == current:
            break
        try:
            current.relative_to(stop)
        except ValueError:
            break
        current = current.parent
    return None


def discover_files_under(
    root: Path,
    filename: str,
    *,
    max_depth: int = 6,
) -> list[Path]:
    """Find *filename* under *root*, excluding dependency/vendor trees."""
    root = root.resolve()
    found: list[Path] = []
    try:
        for path in root.rglob(filename):
            if not path.is_file():
                continue
            if any(part in _DEP_INSTALL_IGNORE_DIRS for part in path.parts):
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if len(rel.parts) - 1 > max_depth:
                continue
            found.append(path)
    except OSError:
        return []
    return sorted(found, key=lambda p: (len(p.relative_to(root).parts), str(p)))


def _format_ambiguous_install_hint(
    label: str,
    paths: list[Path],
    project_root: Path,
    *,
    install_dir_key: str = "install_dir",
) -> str:
    rels = [p.relative_to(project_root).as_posix() for p in paths[:12]]
    extra = f" (+{len(paths) - 12} more)" if len(paths) > 12 else ""
    return (
        f"Multiple {label} ({len(paths)}). Pass {install_dir_key}=<folder> "
        f"relative to the project root, or cd into the target folder.\n"
        f"Found: {', '.join(rels)}{extra}"
    )


def resolve_node_install_dir(
    project_root: str,
    cwd: str,
    *,
    install_dir: str | None = None,
    workspace_root: str | None = None,
    plan_project_dir: str | None = None,
) -> tuple[Path | None, str | None]:
    """Directory where npm/pnpm/yarn should run (contains package.json)."""
    root = Path(project_root).resolve()
    stop = plan_install_boundary(
        workspace_root or project_root,
        plan_project_dir,
    )
    cwd_path = Path(cwd).resolve()

    if install_dir:
        target = (root / install_dir.strip("/\\")).resolve()
        pkg = target / "package.json"
        if pkg.is_file():
            return target, None
        return None, (
            f"install_dir '{install_dir}' has no package.json "
            f"(looked at {target})."
        )

    hit = walk_up_find_file(cwd_path, stop, "package.json")
    if hit is not None:
        return hit.parent, None

    candidates = discover_files_under(root, "package.json")
    if len(candidates) == 1:
        return candidates[0].parent, None
    if not candidates:
        return None, (
            "No package.json found under the project. Create one or pass "
            "install_dir=<folder> (any name — not limited to frontend/backend)."
        )
    return None, _format_ambiguous_install_hint(
        "package.json files", candidates, root,
    )


def resolve_python_requirements_path(
    project_root: str,
    cwd: str,
    *,
    requirements_file: str | None = None,
    install_dir: str | None = None,
    workspace_root: str | None = None,
    plan_project_dir: str | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve a requirements.txt (or explicit *requirements_file*) for pip."""
    root = Path(project_root).resolve()
    stop = plan_install_boundary(
        workspace_root or project_root,
        plan_project_dir,
    )
    cwd_path = Path(cwd).resolve()

    if requirements_file:
        rel = requirements_file.replace("\\", "/")
        for base in (root, cwd_path):
            candidate = (base / rel).resolve()
            if candidate.is_file():
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                return candidate, None
        return None, f"requirements_file '{requirements_file}' not found under {root}."

    if install_dir:
        target = Path(
            resolve_venv_project_root(
                str(root),
                install_dir=install_dir,
                cwd=str(cwd_path),
            ),
        )
        req = target / "requirements.txt"
        if req.is_file():
            return req, None
        for marker in _PYTHON_MARKERS:
            if marker == "requirements.txt":
                continue
            if (target / marker).is_file():
                return None, (
                    f"install_dir '{install_dir}' has {marker} but no "
                    "requirements.txt — pass requirements_file= explicitly."
                )
        return None, (
            f"install_dir '{install_dir}' has no requirements.txt "
            f"(looked at {target})."
        )

    hit = walk_up_find_file(cwd_path, stop, "requirements.txt")
    if hit is not None:
        return hit, None

    for marker in _PYTHON_MARKERS:
        if marker == "requirements.txt":
            continue
        py_hit = walk_up_find_file(cwd_path, stop, marker)
        if py_hit is not None:
            return None, (
                f"Found {marker} near CWD but project_install expects "
                "requirements.txt for bulk install — create requirements.txt "
                "or pass requirements_file=."
            )

    candidates = discover_files_under(root, "requirements.txt")
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, (
            "No requirements.txt found under the project. Create one or pass "
            "install_dir= / requirements_file=."
        )
    return None, _format_ambiguous_install_hint(
        "requirements.txt files", candidates, root,
    )


def detect_ecosystem(
    project_root: str,
    *,
    cwd: str | None = None,
    install_dir: str | None = None,
    workspace_root: str | None = None,
    plan_project_dir: str | None = None,
    package: str | None = None,
) -> str:
    """Return ``python``, ``node``, or ``unknown`` using CWD proximity, not folder names."""
    if package and looks_like_pypi_package_spec(package):
        return "python"

    root = Path(project_root).resolve()
    effective_cwd = Path(cwd or project_root).resolve()
    stop = plan_install_boundary(
        workspace_root or project_root,
        plan_project_dir,
    )

    if install_dir:
        target = (root / install_dir.strip("/\\")).resolve()
        if (target / "package.json").is_file():
            return "node"
        if (target / "requirements.txt").is_file():
            return "python"
        if any((target / m).exists() for m in _PYTHON_MARKERS):
            return "python"
        return "unknown"

    node_depth: int | None = None
    py_depth: int | None = None
    current = effective_cwd
    depth = 0
    while True:
        if node_depth is None and (current / "package.json").is_file():
            node_depth = depth
        if py_depth is None:
            if (current / "requirements.txt").is_file():
                py_depth = depth
            elif any((current / m).exists() for m in _PYTHON_MARKERS if m != "requirements.txt"):
                py_depth = depth
        if node_depth is not None and py_depth is not None:
            break
        if current == stop or current.parent == current:
            break
        try:
            current.relative_to(stop)
        except ValueError:
            break
        current = current.parent
        depth += 1

    if node_depth is not None and py_depth is not None:
        if node_depth < py_depth:
            return "node"
        if py_depth < node_depth:
            return "python"
        return "python"
    if node_depth is not None:
        return "node"
    if py_depth is not None:
        return "python"

    node_dir, _ = resolve_node_install_dir(
        project_root,
        str(effective_cwd),
        workspace_root=workspace_root,
        plan_project_dir=plan_project_dir,
    )
    req_path, _ = resolve_python_requirements_path(
        project_root,
        str(effective_cwd),
        workspace_root=workspace_root,
        plan_project_dir=plan_project_dir,
    )
    has_node = node_dir is not None
    has_python = req_path is not None
    if has_python and not has_node:
        return "python"
    if has_node and not has_python:
        if cwd_has_python_work(effective_cwd, stop):
            return "python"
        return "node"
    if has_python and has_node:
        return "python"
    if cwd_has_python_work(effective_cwd, stop):
        return "python"
    return "unknown"


def resolve_project_root(
    cwd: str,
    workspace_root: str,
    *,
    plan_project_dir: str | None = None,
) -> str | None:
    """Walk up from *cwd* to find a project root (marker file present).

    Stops at *workspace_root*. Falls back to *cwd* when it differs from
    *workspace_root* (typical for plan-locked delegate CWD).

    When still unresolved at the workspace root, *plan_project_dir* (from the
    agent's active or most recent plan) is the authoritative project folder
    for that agent — each agent has an isolated workspace, so this is preferred
    over guessing among sibling directories.
    """
    cwd_path = Path(cwd).resolve()
    workspace = Path(workspace_root).resolve()

    plan_dir = (plan_project_dir or "").strip().strip("/\\")
    if plan_dir:
        planned = (workspace / plan_dir).resolve()
        try:
            planned.relative_to(workspace)
            cwd_path.relative_to(planned)
            return str(planned)
        except ValueError:
            pass

    current = cwd_path
    while True:
        for marker in PROJECT_MARKERS:
            if (current / marker).exists():
                return str(current)
        if (current / "package.json").exists() or (current / "requirements.txt").exists():
            return str(current)
        if any((current / m).exists() for m in _PYTHON_MARKERS):
            return str(current)
        if current == workspace or current.parent == current:
            break
        current = current.parent

    if cwd_path != workspace:
        return str(cwd_path)

    if plan_dir:
        planned = (workspace / plan_dir).resolve()
        try:
            planned.relative_to(workspace)
        except ValueError:
            pass
        else:
            return str(planned)

    candidates: list[Path] = []
    try:
        for child in sorted(workspace.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if any((child / marker).exists() for marker in PROJECT_MARKERS):
                candidates.append(child)
    except OSError:
        return None

    if len(candidates) == 1:
        child = candidates[0]
        # Full app folder (package.json / .git) → that child is the project.
        # Sole subfolder with only requirements.txt → monorepo; workspace is root.
        if (child / "package.json").exists() or (child / ".git").exists():
            return str(child)
        if cwd_path == workspace:
            return str(workspace)
        return str(child)
    if len(candidates) > 1:
        return str(workspace)
    return None


def list_partial_python_scaffolds(workspace_root: str) -> list[Path]:
    """Subdirs with Python work but no dependency manifest yet."""
    workspace = Path(workspace_root).resolve()
    found: list[Path] = []
    try:
        for child in sorted(workspace.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in _DEP_INSTALL_IGNORE_DIRS:
                continue
            if any((child / m).is_file() for m in _PYTHON_MARKERS):
                continue
            if (child / "package.json").is_file():
                continue
            if list(child.glob("*.py"))[:1]:
                found.append(child)
    except OSError:
        pass
    return found


def resolve_venv_project_root(
    project_root: str,
    *,
    install_dir: str | None = None,
    cwd: str | None = None,
) -> str:
    """Directory that owns ``.venv`` for this install (may differ from monorepo root)."""
    if not install_dir:
        return project_root
    root = Path(project_root).resolve()
    rel = install_dir.strip("/\\")
    leaf = Path(rel).name
    if root.name == leaf:
        return str(root)
    if cwd:
        cw = Path(cwd).resolve()
        target = (root / rel).resolve()
        if cw == target:
            return str(cw)
        try:
            cw.relative_to(root)
        except ValueError:
            pass
        else:
            if cw.name == leaf:
                return str(cw)
    return str((root / rel).resolve())


def scaffold_requirements_line(project_dir: Path, package: str) -> Path | None:
    """Create requirements.txt with *package* when missing. Returns path or None."""
    text = (package or "").strip()
    if not text or parse_pip_requirements_ref(text):
        return None
    req = project_dir / "requirements.txt"
    if req.is_file():
        return None
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        req.write_text(f"{text}\n", encoding="utf-8")
        return req
    except OSError:
        return None


def format_post_root_install_hint(workspace_root: str, install_dir: str = "") -> str:
    """Breadcrumb when deps were installed at repo root but app lives in a subdir."""
    if install_dir:
        return ""
    scaffolds = list_partial_python_scaffolds(workspace_root)
    if len(scaffolds) != 1:
        return ""
    name = scaffolds[0].name
    return (
        f"\n[INSTALL NOTE] App stack likely lives in `{name}/`. "
        f"Next: project_install(install_dir='{name}') or cd {name} before bash/python."
    )


def format_scaffold_before_install_hint(
    workspace_root: str,
    *,
    partial_scaffolds: list[Path] | None = None,
    install_dir: str = "",
) -> str:
    """Tell the model the correct scaffold → install order."""
    scaffolds = partial_scaffolds or list_partial_python_scaffolds(workspace_root)
    if install_dir:
        return (
            f"Create {install_dir}/requirements.txt first (list dependencies), "
            f"then project_install(install_dir='{install_dir}') — or pass "
            f"package= with install_dir= to auto-create requirements.txt."
        )
    if len(scaffolds) == 1:
        name = scaffolds[0].name
        return (
            f"Order: 1) write {name}/requirements.txt  "
            f"2) project_install(install_dir='{name}'). "
            f"Or project_install(package='...', install_dir='{name}') to "
            f"auto-scaffold requirements.txt."
        )
    if scaffolds:
        names = ", ".join(p.name for p in scaffolds[:4])
        return (
            f"Order: write requirements.txt in the target folder ({names}), "
            f"then project_install(install_dir='<folder>')."
        )
    return (
        "Scaffold the project first (requirements.txt or package.json), "
        "then project_install()."
    )


def format_project_root_hint(
    workspace_root: str,
    candidates: list[Path],
    *,
    plan_project_dir: str = "",
) -> str:
    """Human hint when project root could not be resolved."""
    plan_dir = (plan_project_dir or "").strip().strip("/\\")
    if plan_dir:
        return (
            f"Plan project_dir is '{plan_dir}' but that folder is missing or "
            "unreachable. Create it with plan(action='create') or scaffold "
            "files there, then retry project_install."
        )
    if not candidates:
        partial = list_partial_python_scaffolds(workspace_root)
        if partial:
            return format_scaffold_before_install_hint(
                workspace_root,
                partial_scaffolds=partial,
            )
        return (
            "Scaffold the project first (package.json, requirements.txt, or "
            "pyproject.toml), or set plan project_dir before installing."
        )
    names = ", ".join(c.name for c in candidates[:6])
    extra = f" (+{len(candidates) - 6} more)" if len(candidates) > 6 else ""
    return (
        f"Multiple project folders under workspace: {names}{extra}. "
        "Set plan project_dir or cd into the target folder, then retry."
    )


def list_workspace_project_candidates(workspace_root: str) -> list[Path]:
    """Immediate child directories that look like app projects."""
    workspace = Path(workspace_root).resolve()
    found: list[Path] = []
    try:
        for child in sorted(workspace.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if any((child / marker).exists() for marker in PROJECT_MARKERS):
                found.append(child)
    except OSError:
        pass
    return found


def find_requirements_file(
    project_root: str,
    *,
    cwd: str | None = None,
    install_dir: str | None = None,
    workspace_root: str | None = None,
    plan_project_dir: str | None = None,
) -> Path | None:
    """Locate requirements.txt via CWD walk, install_dir, or discovery."""
    path, _err = resolve_python_requirements_path(
        project_root,
        cwd or project_root,
        install_dir=install_dir,
        workspace_root=workspace_root,
        plan_project_dir=plan_project_dir,
    )
    return path


def find_package_json(
    project_root: str,
    *,
    cwd: str | None = None,
    install_dir: str | None = None,
    workspace_root: str | None = None,
    plan_project_dir: str | None = None,
) -> Path | None:
    """Locate package.json via CWD walk, install_dir, or discovery."""
    node_dir, _err = resolve_node_install_dir(
        project_root,
        cwd or project_root,
        install_dir=install_dir,
        workspace_root=workspace_root,
        plan_project_dir=plan_project_dir,
    )
    if node_dir is None:
        return None
    return node_dir / "package.json"


def parse_pip_requirements_ref(package: str) -> str | None:
    """Return requirements file path when *package* is ``-r path`` or ``--requirement path``."""
    text = (package or "").strip()
    if not text:
        return None
    for pattern in (
        r"^-r\s+(.+)$",
        r"^--requirement\s+(.+)$",
        r"^-r\s*['\"](.+)['\"]$",
    ):
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("'\"")
    return None


_RUNTIME_ONLY_ROOT_PACKAGES = frozenset({"anthropic", "openai"})


def list_nested_python_package_dirs(
    workspace_root: str,
    *,
    max_depth: int = 2,
) -> list[Path]:
    """Subdirectories under *workspace_root* with their own Python manifests."""
    workspace = Path(workspace_root).resolve()
    found: set[Path] = set()
    for filename in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"):
        for path in discover_files_under(workspace, filename, max_depth=max_depth):
            parent = path.parent.resolve()
            if parent != workspace:
                found.add(parent)
    return sorted(found, key=lambda p: (len(p.relative_to(workspace).parts), str(p)))


def _path_is_under(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_nearest_nested_python_package(
    cwd: str,
    scope_root: str,
) -> Path | None:
    """Pick the nested Python package closest to *cwd* within *scope_root*."""
    scope = Path(scope_root).resolve()
    cwd_path = Path(cwd).resolve()
    nested = list_nested_python_package_dirs(str(scope))
    if not nested:
        return None
    if len(nested) == 1:
        return nested[0]

    inside = [
        p for p in nested
        if cwd_path == p.resolve() or _path_is_under(p, cwd_path)
    ]
    if len(inside) == 1:
        return inside[0]

    if cwd_path == scope:
        return None

    # cwd between scope and packages (e.g. monorepo/apps/foo without manifest)
    descendants = [
        p for p in nested if _path_is_under(cwd_path, p)
    ]
    if len(descendants) == 1:
        return descendants[0]
    return None


def format_ambiguous_venv_hint(scope_root: str) -> str:
    nested = list_nested_python_package_dirs(scope_root)
    names = ", ".join(p.name for p in nested[:6])
    return (
        f"Multiple Python packages ({names}). "
        "cd into one package folder or pass project_install(install_dir='<folder>')."
    )


def root_requirements_is_runtime_only(requirements_path: Path) -> bool:
    """True when root requirements.txt only lists agent-runtime SDK deps."""
    try:
        lines = [
            line.strip()
            for line in requirements_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError:
        return False
    if not lines or len(lines) > 3:
        return False
    for line in lines:
        base = line.split("[")[0].split("==")[0].split(">=")[0].strip().lower()
        if base not in _RUNTIME_ONLY_ROOT_PACKAGES:
            return False
    return True


def resolve_effective_venv_root(
    cwd: str,
    workspace_root: str,
    *,
    plan_project_dir: str | None = None,
    install_dir: str | None = None,
) -> str | None:
    """Directory that should own ``.venv`` for the current working directory."""
    if install_dir:
        project_root = resolve_project_root(
            cwd,
            workspace_root,
            plan_project_dir=plan_project_dir,
        ) or workspace_root
        return resolve_venv_project_root(
            project_root,
            install_dir=install_dir,
            cwd=cwd,
        )

    cwd_path = Path(cwd).resolve()
    stop = plan_install_boundary(workspace_root, plan_project_dir)
    stop_path = stop.resolve()

    nested = list_nested_python_package_dirs(str(stop_path))
    nearest = resolve_nearest_nested_python_package(cwd, str(stop_path))
    if nearest is not None and nearest.resolve() != stop_path:
        return str(nearest)

    owner: Path | None = None
    current = cwd_path
    while True:
        has_py = (current / "requirements.txt").is_file() or any(
            (current / m).is_file()
            for m in _PYTHON_MARKERS
            if m != "requirements.txt"
        )
        if has_py:
            owner = current
            break
        if current == stop_path or current.parent == current:
            break
        try:
            current.relative_to(stop_path)
        except ValueError:
            break
        current = current.parent

    if owner is None:
        return resolve_project_root(
            cwd,
            workspace_root,
            plan_project_dir=plan_project_dir,
        )

    at_monorepo_root = owner.resolve() == stop_path
    if at_monorepo_root and nested:
        req = owner / "requirements.txt"
        if len(nested) == 1:
            return str(nested[0])
        if req.is_file() and root_requirements_is_runtime_only(req):
            if nearest is not None:
                return str(nearest)
            return None
        if len(nested) > 1:
            logger.info(
                "[project_runtime] Ambiguous monorepo venv at root; "
                "nested packages: %s",
                ", ".join(p.name for p in nested[:4]),
            )
            return None
    return str(owner)


def resolve_guardrail_workspace(
    cwd: str,
    workspace_root: str,
    *,
    plan_project_dir: str | None = None,
) -> str:
    """Monorepo/project root for dual-.venv guardrails (not the agent home)."""
    ws_path = Path(workspace_root).resolve()
    cwd_path = Path(cwd).resolve()
    plan_dir = (plan_project_dir or "").strip().strip("/\\")
    if plan_dir:
        planned = (ws_path / plan_dir).resolve()
        try:
            planned.relative_to(ws_path)
            cwd_path.relative_to(planned)
            return str(planned)
        except ValueError:
            pass

    parent = cwd_path.parent
    if parent != cwd_path:
        nested = list_nested_python_package_dirs(str(parent))
        if nested and cwd_path in nested:
            return str(parent.resolve())

    project_root = resolve_project_root(
        cwd,
        workspace_root,
        plan_project_dir=plan_project_dir,
    )
    if project_root:
        return str(Path(project_root).resolve())
    return str(plan_install_boundary(workspace_root, plan_project_dir).resolve())


def dual_venv_conflict_message(
    workspace_root: str,
    *,
    target_venv_root: str,
) -> str | None:
    """Return a guardrail error when multiple ``.venv`` trees would conflict."""
    workspace = Path(workspace_root).resolve()
    target = Path(target_venv_root).resolve()
    nested = list_nested_python_package_dirs(str(workspace))
    if not nested:
        return None

    root_venv = workspace / ".venv"
    nested_with_venv = [p for p in nested if (p / ".venv").is_dir()]

    if root_venv.is_dir() and nested_with_venv:
        nested_names = ", ".join(f"{p.name}/.venv" for p in nested_with_venv[:3])
        return (
            "Dual .venv conflict: repo root `.venv` and nested "
            f"{nested_names}. Use ONE venv per Python package — remove the "
            "extra `.venv` and install with "
            f"project_install(install_dir='{nested_with_venv[0].name}')."
        )

    if target == workspace and nested:
        names = ", ".join(p.name for p in nested[:4])
        return (
            f"Do not create `.venv` at the monorepo root when Python packages "
            f"live in {names}. Use project_install(install_dir='<folder>') "
            f"or cd into that folder first."
        )
    return None


def check_venv_location_allowed(
    venv_root: str,
    workspace_root: str,
) -> str | None:
    """Block new venv creation at the wrong monorepo level."""
    venv_dir = Path(venv_root).resolve() / ".venv"
    if venv_dir.is_dir():
        return None
    return dual_venv_conflict_message(
        workspace_root,
        target_venv_root=venv_root,
    )


def _venv_paths(project_root: str) -> tuple[Path, Path, Path]:
    venv_dir = Path(project_root) / ".venv"
    bin_dir = venv_dir / ("Scripts" if _IS_WINDOWS else "bin")
    python_exe = bin_dir / ("python.exe" if _IS_WINDOWS else "python")
    return venv_dir, bin_dir, python_exe


def ensure_project_venv(
    project_root: str,
    *,
    workspace_root: str | None = None,
) -> tuple[str | None, str | None]:
    """Ensure ``project_root/.venv`` exists.

    Returns ``(bin_dir, python_exe)`` as strings, or ``(None, None)`` on failure.
    """
    if not project_root:
        return None, None

    venv_dir, bin_dir, python_exe = _venv_paths(project_root)

    if python_exe.exists():
        return str(bin_dir), str(python_exe)

    ws = workspace_root or project_root
    blocked = check_venv_location_allowed(project_root, ws)
    if blocked:
        logger.warning("[project_runtime] Blocked venv creation: %s", blocked)
        return None, None

    try:
        logger.info("[project_runtime] Creating project venv: %s", venv_dir)
        _venv_mod.create(
            str(venv_dir),
            with_pip=True,
            system_site_packages=False,
        )
        _ensure_gitignore(project_root)
        if python_exe.exists():
            return str(bin_dir), str(python_exe)
    except Exception as exc:
        logger.warning("[project_runtime] Failed to create project venv: %s", exc)

    return None, None


def ensure_gitignore_venv(project_root: str) -> None:
    """Public wrapper for gitignore helper."""
    _ensure_gitignore(project_root)


def _ensure_gitignore(project_root: str) -> None:
    gi = Path(project_root) / ".gitignore"
    try:
        if gi.exists():
            content = gi.read_text(encoding="utf-8", errors="replace")
            if ".venv" in content:
                return
        with gi.open("a", encoding="utf-8") as f:
            f.write("\n.venv/\n")
    except Exception:
        pass


def detect_node_package_manager(project_root: str) -> str:
    """Return ``pnpm``, ``yarn``, or ``npm`` based on lockfiles."""
    root = Path(project_root)
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"
