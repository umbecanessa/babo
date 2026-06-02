"""Crystallize Skill agent tool -- convert instruction-based skills to native NLS plugins.

Allows the agent to consciously trigger crystallization of an AgentSkill
that it has practiced enough.  Checks readiness thresholds before
generating code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


class CrystallizeSkillTool:
    """Agent tool for converting instruction-based skills to native plugins."""

    @property
    def name(self) -> str:
        return "crystallize_skill"

    @property
    def description(self) -> str:
        return (
            "Convert an instruction-based AgentSkill into a native NLS plugin. "
            "Only works for skills you have used enough times with high success. "
            "The generated plugin goes through the approval pipeline before activation."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the AgentSkill to crystallize.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you want to crystallize this skill.",
                },
            },
            "required": ["skill_name"],
        }

    def __init__(
        self,
        skill_loader: Any = None,
        calibrator: Any = None,
        ans: Any = None,
        data_dir: str = "data",
        theory_of_mind: Any = None,
        narrative_self: Any = None,
        working_memory: Any = None,
    ) -> None:
        self._skill_loader = skill_loader
        self._calibrator = calibrator
        self._ans = ans
        self._data_dir = data_dir
        self._skill_name = "crystallize"
        self._theory_of_mind = theory_of_mind
        self._narrative_self = narrative_self
        self._working_memory = working_memory

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        skill_name = params.get("skill_name", "").strip()
        reason = params.get("reason", "")

        if not skill_name:
            return ToolResult(
                content="'skill_name' parameter is required.",
                is_error=True,
            )

        if not self._skill_loader:
            return ToolResult(
                content="Skill loader not available. Cannot crystallize.",
                is_error=True,
            )

        loaded = self._skill_loader.skills.get(skill_name)
        if not loaded or not loaded.meta:
            return ToolResult(
                content=f"Skill '{skill_name}' not found.",
                is_error=True,
            )

        if loaded.meta.skill_type not in ("agentskill", "hybrid"):
            return ToolResult(
                content=f"Skill '{skill_name}' is already a native plugin. "
                        "Only instruction-based skills can be crystallized.",
                is_error=True,
            )

        from nls.brain.crystallization import (
            CrystallizationConfig,
            evaluate_candidates,
            generate_native_skill,
        )

        config_path = Path("nls/config/crystallization.json")
        config = CrystallizationConfig.load(config_path)

        skill_tracker = {}
        if self._calibrator and hasattr(self._calibrator, "domain_tracker"):
            dt = self._calibrator.domain_tracker
            if hasattr(dt, "skill_encounters"):
                encounters = dt.skill_encounters.get(skill_name)
                if encounters:
                    skill_tracker[skill_name] = (
                        encounters.model_dump()
                        if hasattr(encounters, "model_dump")
                        else encounters
                    )

        if not skill_tracker.get(skill_name):
            return ToolResult(
                content=f"No usage data found for skill '{skill_name}'. "
                        "Use the skill more before attempting crystallization.",
                is_error=True,
            )

        task_summaries: list[dict[str, Any]] = []
        if self._ans and hasattr(self._ans, "_recent_tasks"):
            task_summaries = list(self._ans._recent_tasks[-config.task_memory_lookback:])

        # Gather ToM context for crystallization biasing (IR-9)
        user_interests: dict[str, float] | None = None
        user_expertise: dict[str, float] | None = None
        goal_skills: list[str] | None = None

        if self._theory_of_mind is not None:
            try:
                um = self._theory_of_mind.get_user()
                if um is not None:
                    interests = um.top_interests(10) if hasattr(um, "top_interests") else []
                    user_interests = {t: 1.0 for t in interests} if interests else None
                    if hasattr(um, "expertise"):
                        user_expertise = um.expertise if um.expertise else None
            except Exception:
                pass

        if self._working_memory is not None:
            try:
                goals = getattr(self._working_memory, "_goals", [])
                goal_skills = [
                    g.content for g in goals
                    if hasattr(g, "content") and "crystallize" in g.content.lower()
                ] or None
            except Exception:
                pass

        candidates = evaluate_candidates(
            skill_tracker=skill_tracker,
            task_summaries=task_summaries,
            config=config,
            user_interests=user_interests,
            user_expertise=user_expertise,
            goal_crystallize_skills=goal_skills,
        )

        candidate = next((c for c in candidates if c.skill_name == skill_name), None)
        if not candidate:
            return ToolResult(
                content=f"Could not evaluate skill '{skill_name}'.",
                is_error=True,
            )

        if candidate.readiness_score < 0.5:
            return ToolResult(
                content=(
                    f"Skill '{skill_name}' is not ready for crystallization.\n"
                    f"Readiness: {candidate.readiness_score:.0%}\n"
                    f"Uses: {candidate.total_uses} (need {config.min_total_uses})\n"
                    f"Success rate: {candidate.success_rate:.0%} (need {config.min_success_rate:.0%})\n"
                    f"Myelination: {candidate.myelination_score:.2f} (need {config.min_myelination_score})\n"
                    f"Keep practicing to improve readiness."
                ),
            )

        instructions = loaded.meta.instructions or ""

        episodes: list[dict[str, Any]] | None = None
        if self._narrative_self is not None:
            try:
                eps = getattr(self._narrative_self, "_episodes", [])
                episodes = [
                    {"summary": getattr(e, "summary", str(e)), "tag": getattr(e, "tag", "")}
                    for e in eps[-5:]
                ] if eps else None
            except Exception:
                pass

        files = generate_native_skill(candidate, instructions, episodes=episodes)

        native_name = f"{skill_name}-native"
        output_dir = Path(self._data_dir) / "skills" / native_name
        output_dir.mkdir(parents=True, exist_ok=True)

        for filename, content in files.items():
            (output_dir / filename).write_text(content, encoding="utf-8")

        logger.info(
            "Crystallized skill '%s' -> '%s' (readiness=%.2f, reason=%s)",
            skill_name, native_name, candidate.readiness_score, reason,
        )

        return ToolResult(
            content=(
                f"Skill '{skill_name}' crystallized into native plugin '{native_name}'!\n"
                f"Readiness score: {candidate.readiness_score:.0%}\n"
                f"Generated files: {', '.join(files.keys())}\n"
                f"The plugin is pending review before activation."
            ),
            details={
                "skill_name": skill_name,
                "native_name": native_name,
                "readiness_score": candidate.readiness_score,
                "files": list(files.keys()),
            },
        )


def create_crystallize_tool(
    skill_loader: Any = None,
    calibrator: Any = None,
    ans: Any = None,
    data_dir: str = "data",
    theory_of_mind: Any = None,
    narrative_self: Any = None,
    working_memory: Any = None,
) -> CrystallizeSkillTool:
    """Factory for the crystallize skill tool."""
    return CrystallizeSkillTool(
        skill_loader=skill_loader,
        calibrator=calibrator,
        ans=ans,
        data_dir=data_dir,
        theory_of_mind=theory_of_mind,
        narrative_self=narrative_self,
        working_memory=working_memory,
    )
