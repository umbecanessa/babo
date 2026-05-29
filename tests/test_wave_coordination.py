"""Wave coordination — tech stack block and structured compliance checks."""

from __future__ import annotations

from pathlib import Path

from nls.agentic.wave_coordination import (
    build_tech_stack_block,
    detect_tech_stack_drift,
    expand_wave0_scaffold_paths,
    resolve_step_owned_paths,
)
from nls.agentic.plan_store import PlanStep


def test_tech_stack_block_includes_requirements():
    block = build_tech_stack_block(
        "Backend: Node.js + Express. Frontend: React.",
        plan_title="My App",
    )
    assert "MANDATORY" in block
    assert "Express" in block
    assert "My App" in block


def test_owned_paths_from_step_only():
    from nls.agentic.plan_store import PlanStep

    step = PlanStep(
        label="Assembly AI transcription integration",
        owned_paths=["packages/server/services/transcription.py"],
    )
    paths = resolve_step_owned_paths(step)
    assert paths == ["packages/server/services/transcription.py"]


def test_normalize_strips_fictional_subfolder():
    from nls.agentic.wave_coordination import normalize_project_relative_path

    pd = "icf-coaching-session-evaluation-platform"
    assert normalize_project_relative_path(
        "coaching-eval-platform/backend/", pd,
    ) == "backend/"
    assert normalize_project_relative_path(
        "icf-coaching-session-evaluation-platform/backend/main.py", pd,
    ) == "backend/main.py"
    assert normalize_project_relative_path(".gitignore", pd) == ".gitignore"


def test_validate_flags_fictional_subfolder():
    from nls.agentic.wave_coordination import validate_step_paths_for_project

    warnings = validate_step_paths_for_project(
        "icf-coaching-session-evaluation-platform",
        ["coaching-eval-platform/backend/"],
        step_id="step-1",
    )
    assert any("coaching-eval-platform" in w for w in warnings)


def test_resolve_owned_paths_normalizes_with_project_dir():
    from nls.agentic.plan_store import PlanStep

    step = PlanStep(
        output_files=[
            "coaching-eval-platform/backend/",
            "coaching-eval-platform/README.md",
        ],
    )
    paths = resolve_step_owned_paths(
        step, "icf-coaching-session-evaluation-platform",
    )
    assert "backend/" in paths
    assert "README.md" in paths
    assert not any(p.startswith("coaching-eval-platform") for p in paths)


def test_owned_paths_empty_without_step_fields():
    assert resolve_step_owned_paths(None) == []


def test_wave0_expands_empty_owned_paths():
    expanded = expand_wave0_scaffold_paths([])
    assert ".gitignore" in expanded
    assert "backend/" in expanded
    assert "." in expanded


def test_wave0_expands_root_files_when_step_owns_backend():
    step = PlanStep(
        label="Scaffolding",
        owned_paths=["backend/", "frontend/"],
    )
    paths = resolve_step_owned_paths(step, wave_index=0)
    assert "backend/" in paths
    assert ".gitignore" in paths
    assert "README.md" in paths


def test_wave1_does_not_auto_expand_root_scaffold():
    step = PlanStep(
        label="API",
        owned_paths=["backend/src/api/"],
    )
    paths = resolve_step_owned_paths(step, wave_index=1)
    assert paths == ["backend/src/api/"]


def test_detect_stack_drift_undeclared_fastapi(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "packages/server/app").mkdir(parents=True)
    main = root / "packages/server/app/main.py"
    main.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    issues = detect_tech_stack_drift(
        "",
        str(root),
        tech_stack={"backend_framework": "express", "backend_language": "typescript"},
    )
    assert any("fastapi" in i.lower() for i in issues)


def test_detect_stack_requires_structured_stack(tmp_path: Path):
    issues = detect_tech_stack_drift("", str(tmp_path))
    assert any("tech_stack" in i for i in issues)


def test_detect_stack_compliance_express(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "package.json").write_text(
        '{"dependencies": {"express": "^4.0.0"}}',
        encoding="utf-8",
    )
    issues = detect_tech_stack_drift(
        "",
        str(root),
        tech_stack={"backend_framework": "express"},
    )
    assert issues == []


def test_derive_shared_paths_from_project(tmp_path: Path):
    from nls.agentic.wave_coordination import derive_shared_paths

    root = tmp_path / "proj"
    (root / "packages/server/services").mkdir(parents=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / "packages/server/services/__init__.py").write_text("", encoding="utf-8")
    shared = derive_shared_paths(root)
    assert "package.json" in shared
    assert "packages/server/services/__init__.py" in shared
