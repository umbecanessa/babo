"""request_restart tool -- Propose a server restart for user approval.

After the agent creates or modifies skills, it calls this tool.
A skill review is created with metadata about new/changed skills.
The user sees a review card in the chat and approves (restart) or
rejects (skill deleted).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

RESTART_EXIT_CODE = 75

_restart_requested = False


def is_restart_requested() -> bool:
    return _restart_requested


class RequestRestartTool:
    """Propose a server restart after creating/modifying skills."""

    def __init__(self, data_dir: str = "", agent_id: str = "") -> None:
        self._data_dir = data_dir
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "request_restart"

    @property
    def description(self) -> str:
        return (
            "Propose a server restart after creating or modifying skills. "
            "The user sees what skills are new/changed and must approve "
            "before the server restarts and loads them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Short description of what you built and why "
                        "(shown to the user in the review card)."
                    ),
                },
            },
            "required": ["reason"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        reason = (params.get("reason") or "").strip()
        if not reason:
            return ToolResult(
                content="Error: 'reason' is required — describe what you built.",
                is_error=True,
            )

        data_dir = Path(self._data_dir) if self._data_dir else Path("data")
        skills_dir = data_dir / "skills"

        new_skills = self._scan_new_skills(skills_dir)
        if not new_skills:
            # Check if any skills have load errors — tell the agent
            error_msg = ""
            try:
                from server.main import app as _app
                _sl = getattr(_app.state, "skill_loader", None)
                if _sl is not None:
                    _errs = {
                        n: sk.error
                        for n, sk in _sl.skills.items()
                        if sk.status == "error" and sk.error
                    }
                    if _errs:
                        error_msg = (
                            " However, these skills have LOAD ERRORS "
                            "that need fixing:\n"
                            + "\n".join(
                                f"  - {n}: {e}" for n, e in _errs.items()
                            )
                            + "\nFix the code errors and try again."
                        )
            except Exception:
                pass
            return ToolResult(
                content=(
                    "No new or modified skills found in the skills directory. "
                    "Make sure you created your skill package with an __init__.py "
                    f"in {skills_dir}/ before requesting a restart."
                    + error_msg
                ),
                is_error=True,
            )

        review_id = str(uuid.uuid4())[:8]
        review = {
            "id": review_id,
            "reason": reason,
            "timestamp": time.time(),
            "status": "pending",
            "skills": new_skills,
            "skill_count": len(new_skills),
            "created_by": self._agent_id,
        }

        reviews_dir = data_dir / "skill_reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / f"{review_id}.json").write_text(
            json.dumps(review, indent=2), encoding="utf-8",
        )

        skill_names = [s["name"] for s in new_skills]
        logger.info(
            "request_restart: review %s created for skills %s (reason=%s)",
            review_id, skill_names, reason,
        )

        return ToolResult(
            content=(
                f"Skill review created (id: {review_id}). "
                f"{len(new_skills)} new skill(s): {', '.join(skill_names)}. "
                f"The user will review and approve or reject. "
                f"You do NOT need to do anything else — wait for the user."
            ),
            details={
                "type": "skill_review",
                "review_id": review_id,
                "reason": reason,
                "skills": new_skills,
                "skill_count": len(new_skills),
            },
            stop_loop=True,
        )

    def _scan_new_skills(self, skills_dir: Path) -> list[dict[str, Any]]:
        """Find skills that are new or modified since the server loaded them."""
        if not skills_dir.is_dir():
            return []

        loaded_names: set[str] = set()
        known_names: set[str] = set()
        error_skills: dict[str, str] = {}
        server_start_time: float = 0.0
        try:
            from server.main import app
            sl = getattr(app.state, "skill_loader", None)
            if sl is not None:
                loaded_names = {
                    name for name, sk in sl.skills.items()
                    if sk.status == "loaded"
                }
                error_skills = {
                    name: sk.error or "unknown error"
                    for name, sk in sl.skills.items()
                    if sk.status == "error"
                }
                known_names = set(sl.skills.keys())
                server_start_time = getattr(
                    app.state, "start_time", 0.0,
                )
        except Exception:
            pass

        new_skills = []
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            has_init = (entry / "__init__.py").exists()
            has_skill_md = (entry / "SKILL.md").exists()
            if not has_init and not has_skill_md:
                continue
            name = entry.name
            is_clawhub = (entry / ".clawhub").exists()
            if name in known_names:
                # ClawHub-installed skills that were installed after server
                # start should always be included in the review even if
                # they appear in known_names from a previous failed load.
                if is_clawhub:
                    try:
                        marker_mtime = (entry / ".clawhub").stat().st_mtime
                        if server_start_time and marker_mtime > server_start_time:
                            logger.info(
                                "request_restart: ClawHub skill %s installed "
                                "after startup, including in review",
                                name,
                            )
                        else:
                            continue
                    except Exception:
                        continue
                else:
                    # Non-ClawHub skill known to the loader. Check mtime.
                    try:
                        check_file = entry / "__init__.py" if has_init else entry / "SKILL.md"
                        file_mtime = check_file.stat().st_mtime
                        if server_start_time and file_mtime < server_start_time:
                            if name in error_skills:
                                logger.info(
                                    "request_restart: skill %s has load error "
                                    "and was NOT modified this session — skipping. "
                                    "Error: %s",
                                    name, error_skills[name],
                                )
                            continue
                        logger.info(
                            "request_restart: skill %s is known but modified "
                            "after startup (mtime=%.0f > start=%.0f), "
                            "including in review",
                            name, file_mtime, server_start_time,
                        )
                    except Exception:
                        continue

            skill_info: dict[str, Any] = {"name": name, "files": []}
            for f in sorted(entry.rglob("*")):
                if f.is_file() and f.name != ".disabled":
                    skill_info["files"].append({
                        "path": str(f.relative_to(entry)),
                        "size": f.stat().st_size,
                    })

            req = entry / "requirements.txt"
            if req.exists():
                skill_info["dependencies"] = (
                    req.read_text(encoding="utf-8").strip().splitlines()
                )

            try:
                init_text = (entry / "__init__.py").read_text(encoding="utf-8")
                for line in init_text.splitlines():
                    if "description=" in line:
                        desc = line.split("description=")[1].strip().strip('",')
                        skill_info["description"] = desc
                        break
            except Exception:
                pass

            new_skills.append(skill_info)

        return new_skills


def _trigger_shutdown() -> None:
    """Terminate the server process so the desktop runtime can relaunch it."""
    from server.shutdown_trace import record_initiator

    pid = os.getpid()
    record_initiator(
        "agent:request_restart_approved",
        pid=pid,
        exit_code=RESTART_EXIT_CODE,
    )
    logger.info("request_restart: terminating PID %d with exit code %d", pid, RESTART_EXIT_CODE)
    os._exit(RESTART_EXIT_CODE)


def create_request_restart_tool(
    data_dir: str = "",
    agent_id: str = "",
) -> RequestRestartTool:
    return RequestRestartTool(data_dir=data_dir, agent_id=agent_id)
