"""Tests for orchestrator hint ring helpers."""

from nls.agentic.orchestrator_hint import (
    build_orchestrator_ring_ops,
    infer_directive_domain,
)
from nls.brain.sub_cryptex import SUB_RING_ORCHESTRATOR, SubCryptex


def test_infer_directive_domain_finalize():
    assert infer_directive_domain("STOP and call task_complete now") == "finalize"
    assert infer_directive_domain("", action="terminate") == "finalize"


def test_orchestrator_ring_upsert_in_compose():
    sc = SubCryptex(context_budget_tokens=8000)
    sc.upsert_orchestrator_directive(
        "Use path icf-coaching-evaluation-platform/backend/",
        domain="path_fix",
        salience=0.95,
    )
    ctx = sc.compose_context()
    assert ctx
    body = ctx[0]["content"]
    assert SUB_RING_ORCHESTRATOR in body or "ORCHESTRATOR" in body
    assert "icf-coaching" in body


def test_build_orchestrator_ring_ops():
    ops = build_orchestrator_ring_ops("Fix line 44", domain="hint")
    assert ops[0]["ring"] == SUB_RING_ORCHESTRATOR
    assert ops[0]["domain"] == "hint"
