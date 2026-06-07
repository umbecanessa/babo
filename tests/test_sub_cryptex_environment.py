"""SubCryptex inherits orchestrator environment ring for delegates."""

from __future__ import annotations

from nls.brain.cryptex import CryptexMemory, RING_ENVIRONMENT
from nls.brain.sub_cryptex import SubCryptex


def test_spawn_from_parent_copies_environment_ring():
    parent = CryptexMemory()
    parent.upsert_environment(
        "platform_docs",
        "Platform documentation: https://docs.example.test/babo",
        source="test",
    )
    parent.upsert_environment(
        "local_verification",
        "Prove deliverables before task_complete.",
        source="test",
    )

    sub = SubCryptex.spawn_from_parent(
        parent=parent,
        task="Verify backend API",
    )

    env_ring = sub.get_ring(RING_ENVIRONMENT)
    assert env_ring is not None
    slots = env_ring.get_active_slots()
    domains = {s.domain for s in slots}
    assert "platform_docs" in domains
    assert "local_verification" in domains

    ctx = sub.compose_context()
    assert ctx
    body = ctx[0]["content"]
    assert "https://docs.example.test/babo" in body
    assert "task_complete" in body.lower()
