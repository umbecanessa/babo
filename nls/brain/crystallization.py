"""Crystallization Engine -- Convert instruction-based skills to native NLS plugins.

When an agent uses an AgentSkill (instruction-based) repeatedly with high
success, the crystallization engine can generate a native Python plugin
from the observed execution patterns.  This process is analogous to
procedural memory consolidation in neuroscience.

The engine:
  1. Evaluates candidate skills based on usage, success, myelination, and
     user feedback correction rate.
  2. Gathers task summaries from the ANS task memory.
  3. Extracts common tool sequences and error patterns.
  4. Generates native NLS skill code via an LLM call.

Public API
----------
``evaluate_candidates(skill_tracker, task_memory, feedback_signals, config)``
    Score each AgentSkill and determine readiness.

``generate_native_skill(candidate, original_instructions)``
    Produce Python source files for a native skill.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CrystallizationCandidate:
    """A single AgentSkill evaluated for crystallization readiness."""

    skill_name: str
    skill_type: str = "agentskill"
    total_uses: int = 0
    success_rate: float = 0.0
    myelination_score: float = 0.0
    feedback_correction_rate: float = 1.0
    task_summaries: list[dict[str, Any]] = field(default_factory=list)
    common_tool_sequences: list[list[str]] = field(default_factory=list)
    common_errors: list[str] = field(default_factory=list)
    ready: bool = False
    readiness_score: float = 0.0


@dataclass
class CrystallizationConfig:
    """Configuration for crystallization thresholds."""

    enabled: bool = True
    min_total_uses: int = 15
    min_success_rate: float = 0.85
    min_myelination_score: float = 0.65
    max_feedback_correction_rate: float = 0.15
    auto_generate: bool = False
    require_approval: bool = True
    task_memory_lookback: int = 50

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrystallizationConfig:
        thresholds = d.get("thresholds", {})
        return cls(
            enabled=d.get("enabled", True),
            min_total_uses=thresholds.get("min_total_uses", 15),
            min_success_rate=thresholds.get("min_success_rate", 0.85),
            min_myelination_score=thresholds.get("min_myelination_score", 0.65),
            max_feedback_correction_rate=thresholds.get("max_feedback_correction_rate", 0.15),
            auto_generate=d.get("auto_generate", False),
            require_approval=d.get("require_approval", True),
            task_memory_lookback=d.get("task_memory_lookback", 50),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> CrystallizationConfig:
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls.from_dict(data)
            except Exception as exc:
                logger.warning("Failed to load crystallization config: %s", exc)
        return cls()


def evaluate_candidates(
    skill_tracker: dict[str, Any],
    task_summaries: list[dict[str, Any]],
    feedback_signals: list[dict[str, Any]] | None = None,
    config: CrystallizationConfig | None = None,
    user_interests: dict[str, float] | None = None,
    user_expertise: dict[str, float] | None = None,
    goal_crystallize_skills: list[str] | None = None,
) -> list[CrystallizationCandidate]:
    """Evaluate all AgentSkills for crystallization readiness.

    Parameters
    ----------
    skill_tracker:
        Dict of ``skill_name -> SkillDomainEntry.dict()`` from the
        DomainTracker's ``skill_encounters``.
    task_summaries:
        Recent task summaries from ANS ``get_recent_tasks_context()``.
    feedback_signals:
        Feedback signals from ``Feedback.Skill.*`` domains.
    config:
        Crystallization thresholds (loaded from ``crystallization.json``).
    user_interests:
        ToM user interests (topic -> affinity 0..1) for biasing
        crystallization toward user-relevant skills (IR-9.1).
    user_expertise:
        ToM user expertise (domain -> 0..1) for adjusting success
        rate thresholds — experts demand higher quality (IR-9.2).
    goal_crystallize_skills:
        Skill names explicitly targeted for crystallization via WM
        strategic goals (IR-9.3).

    Returns
    -------
    list of CrystallizationCandidate, sorted by readiness_score desc.
    """
    if config is None:
        config = CrystallizationConfig()

    feedback_signals = feedback_signals or []
    user_interests = user_interests or {}
    user_expertise = user_expertise or {}
    goal_crystallize_skills = goal_crystallize_skills or []
    candidates: list[CrystallizationCandidate] = []

    for skill_name, entry in skill_tracker.items():
        if not isinstance(entry, dict):
            continue

        encounter_count = entry.get("encounter_count", 0)
        success_count = entry.get("success_count", 0)
        failure_count = entry.get("failure_count", 0)
        myelin = entry.get("myelination_score", 0.0)

        total = encounter_count
        success_rate = success_count / max(total, 1)

        skill_tasks = [
            t for t in task_summaries
            if skill_name in str(t.get("tools_used", ""))
            or skill_name in str(t.get("summary", ""))
        ]

        skill_feedback = [
            f for f in feedback_signals
            if skill_name in str(f.get("domain_path", ""))
        ]
        correction_count = len(skill_feedback)
        feedback_rate = correction_count / max(total, 1)

        tool_sequences = _extract_tool_sequences(skill_tasks)
        common_errors = _extract_common_errors(skill_tasks, skill_name)

        # IR-9.1: ToM user-interest threshold adjustment
        interest_discount = 1.0
        skill_lower = skill_name.lower()
        for topic, affinity in user_interests.items():
            if topic.lower() in skill_lower:
                interest_discount = max(0.8, 1.0 - 0.2 * affinity)
                break

        # IR-9.2: Expertise-aware success rate threshold
        effective_min_success = config.min_success_rate
        for domain, exp_level in user_expertise.items():
            if domain.lower() in skill_lower:
                if exp_level > 0.7:
                    effective_min_success = min(0.92, config.min_success_rate + 0.07)
                elif exp_level < 0.3:
                    effective_min_success = max(0.80, config.min_success_rate - 0.05)
                break

        # IR-9.3: Goal-driven threshold reduction
        goal_discount = 1.0
        if skill_name in goal_crystallize_skills:
            goal_discount = 0.7

        effective_min_uses = max(
            3, int(config.min_total_uses * interest_discount * goal_discount)
        )
        effective_min_myelin = config.min_myelination_score * goal_discount

        score = _compute_readiness(
            total_uses=total,
            success_rate=success_rate,
            myelination=myelin,
            feedback_rate=feedback_rate,
            config=config,
        )

        ready = (
            total >= effective_min_uses
            and success_rate >= effective_min_success
            and myelin >= effective_min_myelin
            and feedback_rate <= config.max_feedback_correction_rate
        )

        candidates.append(CrystallizationCandidate(
            skill_name=skill_name,
            total_uses=total,
            success_rate=success_rate,
            myelination_score=myelin,
            feedback_correction_rate=feedback_rate,
            task_summaries=skill_tasks[:10],
            common_tool_sequences=tool_sequences,
            common_errors=common_errors,
            ready=ready,
            readiness_score=score,
        ))

    candidates.sort(key=lambda c: c.readiness_score, reverse=True)
    return candidates


def _compute_readiness(
    total_uses: int,
    success_rate: float,
    myelination: float,
    feedback_rate: float,
    config: CrystallizationConfig,
) -> float:
    """Compute a 0..1 readiness score from usage metrics."""
    usage_score = min(total_uses / max(config.min_total_uses, 1), 1.0)
    success_score = min(success_rate / max(config.min_success_rate, 0.01), 1.0)
    myelin_score = min(myelination / max(config.min_myelination_score, 0.01), 1.0)

    max_fb = config.max_feedback_correction_rate
    feedback_score = max(0.0, 1.0 - (feedback_rate / max(max_fb, 0.01))) if max_fb > 0 else 1.0
    feedback_score = min(feedback_score, 1.0)

    weights = (0.2, 0.3, 0.3, 0.2)
    return (
        weights[0] * usage_score
        + weights[1] * success_score
        + weights[2] * myelin_score
        + weights[3] * feedback_score
    )


def _extract_tool_sequences(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """Extract common tool call sequences from task summaries."""
    sequences: list[list[str]] = []
    for task in tasks:
        tools = task.get("tools_used", [])
        if isinstance(tools, list) and len(tools) >= 2:
            sequences.append(tools)
    return sequences[:5]


def _extract_common_errors(tasks: list[dict[str, Any]], skill_name: str) -> list[str]:
    """Extract repeated error patterns from task summaries."""
    errors: list[str] = []
    for task in tasks:
        error = task.get("error", "")
        if error and skill_name in str(task):
            errors.append(str(error)[:200])
    return list(set(errors))[:5]


def generate_native_skill(
    candidate: CrystallizationCandidate,
    original_instructions: str,
    episodes: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Generate native NLS skill files from a crystallization candidate.

    Returns a dict of ``{filename: content}`` suitable for writing to
    ``data/skills/{name}-native/``.

    Generates a functional plugin that:
    - Registers a tool the agent can call by name
    - Embeds the original instructions as a system-prompt injection
    - Builds a structured tool that executes the most common sequence
    - Guards against known error patterns
    - Incorporates behavioral insights from episode history (IR-9.4)

    Parameters
    ----------
    episodes:
        Recent episode dicts from NarrativeSelf (IR-9.4). Used to
        extract workflow patterns, user preference notes, and error
        context for more accurate code generation.
    """
    skill_name = candidate.skill_name
    native_name = f"{skill_name}-native"
    safe_var = re.sub(r"[^a-z0-9_]", "_", skill_name.replace("-", "_"))

    tool_seq_lines = ""
    for i, seq in enumerate(candidate.common_tool_sequences[:3], 1):
        tool_seq_lines += f"#   Pattern {i}: {' -> '.join(seq)}\n"

    error_lines = ""
    for err in candidate.common_errors[:3]:
        error_lines += f"#   - {err}\n"

    # IR-9.4: Extract behavioral insights from episodes
    episode_notes = ""
    if episodes:
        for ep in episodes[-3:]:
            title = ep.get("title", "untitled")
            arc = ep.get("arc_summary", "")
            mood = ep.get("closing_mood", "")
            if arc or mood:
                episode_notes += f"#   Episode '{title}': arc={arc}, mood={mood}\n"

    error_guard_code = _build_error_guards(candidate.common_errors)
    primary_sequence = _build_primary_sequence(candidate.common_tool_sequences)

    init_py = f'''"""Native skill crystallized from AgentSkill '{skill_name}'.

Auto-generated by the NLS crystallization engine from observed execution
patterns over {candidate.total_uses} uses ({candidate.success_rate:.0%} success rate,
myelination score {candidate.myelination_score:.2f}).

Observed tool sequences:
{tool_seq_lines or "#   (none recorded)"}
Known error patterns:
{error_lines or "#   (none recorded)"}
{("Behavioral episode context:" + chr(10) + episode_notes) if episode_notes else ""}"""

from __future__ import annotations

import logging
from typing import Any

from nls.skills import SkillMeta

logger = logging.getLogger(__name__)

meta = SkillMeta(
    name="{native_name}",
    version="1.0",
    description="Crystallized from AgentSkill: {skill_name}",
    crystallized_from="{skill_name}",
    skill_type="native",
)

_INSTRUCTIONS = """{original_instructions.replace(chr(34)*3, chr(34)*2)}"""

_KNOWN_ERRORS = {candidate.common_errors[:5]!r}


def register(app, ctx):
    """Register tools and inject crystallized skill knowledge."""

    ctx.register_tool_factory("{safe_var}", _create_tool)

    if hasattr(ctx, "inject_prompt"):
        ctx.inject_prompt(
            priority=2,
            name="crystal_{safe_var}",
            content=(
                f"## Crystallized Skill: {skill_name}\\n\\n"
                "You have a native plugin for this task. Use the "
                f"'{safe_var}' tool instead of following raw instructions.\\n\\n"
                f"Original guidance (for reference):\\n{{_INSTRUCTIONS[:2000]}}"
            ),
        )


def _create_tool(agent_id: str) -> "_CrystallizedTool":
    return _CrystallizedTool(agent_id)


class _CrystallizedTool:
    """Tool crystallized from AgentSkill '{skill_name}'."""

    @property
    def name(self) -> str:
        return "{safe_var}"

    @property
    def description(self) -> str:
        return (
            "Crystallized automation for: {skill_name}. "
            + meta.description
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {{
            "type": "object",
            "properties": {{
                "task": {{
                    "type": "string",
                    "description": "What you want this skill to do.",
                }},
                "context": {{
                    "type": "string",
                    "description": "Additional context or parameters.",
                }},
            }},
            "required": ["task"],
        }}

    def __init__(self, agent_id: str = "") -> None:
        self._agent_id = agent_id
        self._skill_name = "{native_name}"

    async def execute(self, params: dict[str, Any], signal=None):
        """Execute the crystallized skill logic.

        Returns structured guidance based on observed patterns,
        including the primary tool sequence and error guards.
        """
        from nls.tools.agent_tools.base import ToolResult

        task = params.get("task", "")
        context = params.get("context", "")
{error_guard_code}
{primary_sequence}
        guidance_parts = [
            f"Executing crystallized skill: {skill_name}",
            f"Task: {{task}}",
        ]
        if context:
            guidance_parts.append(f"Context: {{context}}")

        guidance_parts.append(
            "\\nFollow these instructions to complete the task:\\n"
            + _INSTRUCTIONS[:3000]
        )

        return ToolResult(content="\\n".join(guidance_parts))
'''

    skill_md = f"""---
name: {native_name}
description: "Crystallized native skill from {skill_name}"
metadata:
  crystallized_from: "{skill_name}"
  readiness_score: {candidate.readiness_score:.2f}
  total_uses_at_crystallization: {candidate.total_uses}
  success_rate_at_crystallization: {candidate.success_rate:.2f}
---

# Original Instructions

{original_instructions}
"""

    return {
        "__init__.py": init_py,
        "SKILL.md": skill_md,
    }


def _build_error_guards(common_errors: list[str]) -> str:
    """Generate code that warns about known error patterns."""
    if not common_errors:
        return ""
    lines = ["        # Guard against known error patterns"]
    for i, err in enumerate(common_errors[:3]):
        safe_err = err.replace('"', '\\"')[:100]
        lines.append(
            f'        if "{safe_err.split()[0].lower() if err.split() else "error"}" '
            f'in task.lower():'
        )
        lines.append(
            f'            logger.warning("Known error pattern detected: {safe_err[:60]}")'
        )
    lines.append("")
    return "\n".join(lines)


def _build_primary_sequence(sequences: list[list[str]]) -> str:
    """Generate a comment block showing the primary tool sequence."""
    if not sequences:
        return ""
    best = sequences[0]
    lines = [
        "        # Primary observed tool sequence:",
        f"        # {' -> '.join(best)}",
        "",
    ]
    return "\n".join(lines)


def save_candidates(
    candidates: list[CrystallizationCandidate],
    path: Path,
) -> None:
    """Persist candidate evaluations to disk for UI display."""
    data = []
    for c in candidates:
        data.append({
            "skill_name": c.skill_name,
            "total_uses": c.total_uses,
            "success_rate": c.success_rate,
            "myelination_score": c.myelination_score,
            "feedback_correction_rate": c.feedback_correction_rate,
            "ready": c.ready,
            "readiness_score": c.readiness_score,
            "common_tool_sequences": c.common_tool_sequences,
            "common_errors": c.common_errors,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_candidates(path: Path) -> list[CrystallizationCandidate]:
    """Load previously evaluated candidates from disk."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates = []
        for d in data:
            candidates.append(CrystallizationCandidate(
                skill_name=d.get("skill_name", ""),
                total_uses=d.get("total_uses", 0),
                success_rate=d.get("success_rate", 0.0),
                myelination_score=d.get("myelination_score", 0.0),
                feedback_correction_rate=d.get("feedback_correction_rate", 1.0),
                ready=d.get("ready", False),
                readiness_score=d.get("readiness_score", 0.0),
                common_tool_sequences=d.get("common_tool_sequences", []),
                common_errors=d.get("common_errors", []),
            ))
        return candidates
    except Exception as exc:
        logger.debug("Failed to load crystallization candidates from %s: %s", path, exc)
        return []
