"""Plan tech_stack field and set_requirements / set_tech_stack actions."""

from __future__ import annotations

from pathlib import Path

from nls.agentic.plan_store import Plan, PlanStep, PlanStore
from nls.agentic.wave_coordination import (
    build_tech_stack_block,
    resolve_step_owned_paths,
)


def test_plan_tech_stack_roundtrip(tmp_path: Path):
    store = PlanStore(str(tmp_path / "workspace"))
    plan = store.create_plan(
        title="My App",
        requirements="Build a coaching app",
        tech_stack={
            "backend_language": "typescript",
            "backend_framework": "express",
            "frontend_framework": "react",
        },
        steps=[{"label": "Backend API", "delegatable": True}],
    )
    store.save(plan)
    loaded = store.load(plan.id)
    assert loaded is not None
    assert loaded.tech_stack["backend_framework"] == "express"
    raw = loaded.to_dict()
    assert raw["task"]["tech_stack"]["frontend_framework"] == "react"


def test_build_tech_stack_block_from_plan():
    plan = Plan(
        title="ICF App",
        requirements="Full PRD text here",
        tech_stack={"backend_framework": "express", "orm": "prisma"},
    )
    block = build_tech_stack_block(plan=plan)
    assert "MANDATORY" in block
    assert "express" in block
    assert "prisma" in block
    assert "Full PRD" in block or "PRD text" in block


def test_owned_paths_from_step():
    step = PlanStep(
        id="step-1",
        label="Transcription",
        owned_paths=["backend/services/transcription.py"],
        output_files=["backend/routes/transcription.py"],
    )
    paths = resolve_step_owned_paths(step)
    assert "backend/services/transcription.py" in paths
    assert "backend/routes/transcription.py" in paths


def test_create_plan_step_owned_paths(tmp_path: Path):
    store = PlanStore(str(tmp_path / "workspace"))
    plan = store.create_plan(
        title="App",
        steps=[{
            "label": "API",
            "owned_paths": ["src/api/"],
            "delegatable": True,
        }],
    )
    assert plan.steps[0].owned_paths == ["src/api/"]


def test_owned_paths_no_heuristic_fallback():
    assert resolve_step_owned_paths(None) == []
    step = PlanStep(label="Frontend Development")
    assert resolve_step_owned_paths(step) == []
