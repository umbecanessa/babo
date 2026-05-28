"""Promote skill-discovery rings when the agent is stuck or receives hints."""

from __future__ import annotations

from typing import Any

SKILL_DISCOVERY_SLOT_DOMAIN = "skill.discovery_boost"

SKILL_DISCOVERY_PROMPT = (
    "SKILL DISCOVERY — you appear stuck. Try these before retrying the "
    "same command:\n"
    "1. clawhub(action='search', query='<keyword>') — find community skills\n"
    "2. discover_tools(query='<keyword>') — find deferred tools\n"
    "3. clawhub(action='install', slug='...') then follow skill instructions\n"
    "4. wm(action='borrow', domain='Project.Credential.*') if auth is missing\n"
    "Do NOT loop on the same failing bash command."
)


def trigger_skill_discovery_boost(
    hooks: Any,
    *,
    iteration: int,
    reason: str = "stalled",
    ttl_iters: int = 6,
) -> None:
    """Raise skills/tools ring priority for the next few iterations."""
    ref = getattr(hooks, "_loop_state_ref", None)
    if ref is not None:
        ref["skill_discovery_boost_until"] = max(
            int(ref.get("skill_discovery_boost_until", 0)),
            int(iteration) + ttl_iters,
        )
        ref["skill_discovery_boost"] = True
        ref["skill_discovery_reason"] = reason[:120]

    compositor = getattr(hooks, "_cryptex_compositor", None)
    if compositor is not None and hasattr(compositor, "activate_skill_discovery_boost"):
        try:
            compositor.activate_skill_discovery_boost(reason)
        except Exception:
            pass

    sub = getattr(hooks, "_sub_cryptex", None)
    if sub is not None and hasattr(sub, "activate_skill_discovery_boost"):
        try:
            sub.activate_skill_discovery_boost(reason)
        except Exception:
            pass


def sync_skill_discovery_boost_flag(
    loop_state_ref: dict[str, Any] | None,
    iteration: int,
) -> None:
    """Clear boost once the TTL iteration is passed."""
    if loop_state_ref is None:
        return
    until = int(loop_state_ref.get("skill_discovery_boost_until", 0) or 0)
    if until and iteration > until:
        loop_state_ref.pop("skill_discovery_boost", None)
        loop_state_ref.pop("skill_discovery_boost_until", None)
        loop_state_ref.pop("skill_discovery_reason", None)
    else:
        loop_state_ref["skill_discovery_boost"] = bool(
            until and iteration <= until,
        )
