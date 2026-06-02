"""NLS Agentic Loop System."""

from .loop import run_loop
from .types import (
    AgentEvent,
    AgenticConfig,
    AgenticHooks,
    AgenticResult,
    EventType,
    GenerationResult,
    LoopConfig,
    LoopGuards,
    LoopResult,
    LoopState,
)
from .bridge import LoopHooks, build_config, build_config_v4, build_hooks, build_hooks_v4
from .evaluator import (
    Directive,
    InteroceptiveSnapshot,
    check_guards,
    should_complete,
    should_complete_v4,
)
from .hooks import run_post_hooks, run_pre_hooks
from .plan_store import Plan, PlanStep, PlanStore
from .permissions import PermissionManager

__all__ = [
    "run_loop",
    "AgenticConfig",
    "AgenticHooks",
    "AgenticResult",
    "AgentEvent",
    "EventType",
    "build_config",
    "build_hooks",
    "Directive",
    "InteroceptiveSnapshot",
    "run_pre_hooks",
    "run_post_hooks",
    "PlanStore",
    "Plan",
    "PlanStep",
    "PermissionManager",
    "LoopConfig",
    "LoopState",
    "LoopResult",
    "LoopGuards",
    "GenerationResult",
    "LoopHooks",
    "build_config_v4",
    "build_hooks_v4",
    "should_complete",
    "should_complete_v4",  # deprecated alias
    "check_guards",
]
