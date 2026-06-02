"""Shared rules for project_install vs server_install routing."""

from __future__ import annotations

from typing import Callable

PlanBlocksServerInstallFn = Callable[[], bool]


def should_block_server_install(
    blocked_by_active_plan: bool,
    *,
    for_agent_runtime: bool = False,
) -> bool:
    """Return True when server_install must be rejected.

    During an active plan with a locked tech stack, app dependencies belong in
    the project venv (project_install).  The orchestrator can still extend Babo's
    own runtime by passing ``for_agent_runtime=True`` explicitly.
    """
    if for_agent_runtime:
        return False
    return blocked_by_active_plan


def plan_blocks_server_install(blocked_fn: PlanBlocksServerInstallFn | None) -> bool:
    """Safe wrapper around the wired plan-store callback."""
    if blocked_fn is None:
        return False
    try:
        return bool(blocked_fn())
    except Exception:
        return False


SERVER_INSTALL_BLOCKED_MSG = (
    "Error: server_install is disabled while an active plan with a tech stack "
    "is in progress.\n"
    "Dependencies for the app you are building belong in the project venv:\n\n"
    "  project_install(package='...')\n"
    "  project_install(requirements_file='backend/requirements.txt')\n"
    "  project_install()  # from requirements.txt / package.json\n\n"
    "To install a library for Babo's agent runtime (a new tool/skill "
    "capability — not the app), pass for_agent_runtime=True:\n\n"
    "  server_install(package='...', for_agent_runtime=True)"
)
