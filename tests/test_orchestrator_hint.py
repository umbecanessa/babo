"""Tests for orchestrator hint ring helpers."""

from nls.agentic.orchestrator_hint import (
    build_orchestrator_ring_ops,
    build_orchestrator_chat_hint,
    infer_directive_domain,
    intervention_dict_to_steering_msg,
    normalize_delivery_mode,
    resolve_hint_delivery,
    HINT_DELIVERY_BOTH,
    HINT_DELIVERY_RING,
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


def test_resolve_hint_delivery_defaults_to_both():
    ring, chat, label = resolve_hint_delivery()
    assert ring is True
    assert chat is True
    assert label == HINT_DELIVERY_BOTH


def test_resolve_hint_delivery_ring_only():
    ring, chat, label = resolve_hint_delivery(delivery=HINT_DELIVERY_RING)
    assert ring is True
    assert chat is False
    assert label == HINT_DELIVERY_RING


def test_build_orchestrator_chat_hint():
    msg = build_orchestrator_chat_hint("Stop editing page.tsx")
    assert msg["role"] == "user"
    assert msg["content"].startswith("[ORCHESTRATOR HINT]")
    assert "page.tsx" in msg["content"]


def test_normalize_delivery_mode():
    assert normalize_delivery_mode(None) == HINT_DELIVERY_BOTH
    assert normalize_delivery_mode("ring") == HINT_DELIVERY_RING
    assert normalize_delivery_mode("invalid") is None


def test_intervention_dict_to_steering_msg_both():
    msg = intervention_dict_to_steering_msg({
        "action": "hint",
        "message": "Use edit()",
        "delivery": "both",
    })
    assert msg is not None
    assert "[ORCHESTRATOR HINT]" in msg["content"]


def test_intervention_dict_to_steering_msg_ring_preserved():
    assert intervention_dict_to_steering_msg({
        "action": "hint",
        "message": "Quiet",
        "delivery": "ring",
    }) is None


def test_intervention_dict_to_steering_msg_terminate_preserved():
    assert intervention_dict_to_steering_msg({
        "action": "terminate",
        "message": "Stop",
        "delivery": "both",
    }) is None
