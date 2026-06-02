"""NLS Benchmark — Semantic drift detection for the Genesis Block's core reasoning.

After many epoch merges, the cumulative lossy compression from TIES density
trimming can erode the base model's foundational capabilities (logic, math,
language). This module runs a small battery of "sanity check" prompts against
the hydrated model and scores the results.

If the score drops below a configurable floor, the system warns that the
agent has drifted too far from its Genesis Block and recommends a rollback
or chain reset.

Usage:
    The benchmark runs automatically after `nls merge` and can be triggered
    manually via `nls benchmark <agent_id>`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_cpp import Llama

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Benchmark prompts — minimal set testing core reasoning
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkCase:
    """A single benchmark test case."""

    category: str
    prompt: str
    expected_patterns: list[str]  # Regex patterns — at least one must match
    description: str = ""


# These are intentionally simple — they test whether the BASE capabilities
# of the model are intact, not the agent's learned personality.
CORE_BENCHMARKS: list[BenchmarkCase] = [
    BenchmarkCase(
        category="logic",
        prompt="If all roses are flowers and some flowers fade quickly, can we conclude that all roses fade quickly? Answer with Yes or No and explain briefly.",
        expected_patterns=[
            r"(?i)\bno\b",
            r"(?i)cannot\s+conclude",
            r"(?i)not\s+necessarily",
        ],
        description="Basic syllogism — tests logical reasoning.",
    ),
    BenchmarkCase(
        category="math",
        prompt="What is 17 * 24? Give only the number.",
        expected_patterns=[
            r"408",
        ],
        description="Simple multiplication — tests arithmetic.",
    ),
    BenchmarkCase(
        category="language",
        prompt='What is the past tense of the verb "to run"? Answer with one word.',
        expected_patterns=[
            r"(?i)\bran\b",
        ],
        description="Basic grammar — tests language understanding.",
    ),
    BenchmarkCase(
        category="comprehension",
        prompt="A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost? Give only the amount.",
        expected_patterns=[
            r"\$?0?\.05",
            r"(?i)five\s+cents",
            r"(?i)5\s+cents",
        ],
        description="Classic cognitive reflection test — tests careful reasoning.",
    ),
    BenchmarkCase(
        category="instruction_following",
        prompt='List exactly 3 fruits. Number them 1, 2, 3. Do not list more than 3.',
        expected_patterns=[
            r"1\.",
            r"2\.",
            r"3\.",
        ],
        description="Instruction following — tests the model obeys constraints.",
    ),
    BenchmarkCase(
        category="factual",
        prompt="What is the capital of France? Answer with one word.",
        expected_patterns=[
            r"(?i)\bparis\b",
        ],
        description="Basic factual recall — tests core knowledge.",
    ),
]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    """Result of a single benchmark case."""

    case: BenchmarkCase
    response: str
    passed: bool
    error: str | None = None


@dataclass
class BenchmarkReport:
    """Full benchmark report for a hydrated agent."""

    agent_id: str
    chain_height: int
    total_cases: int
    passed: int
    failed: int
    score: float  # 0.0 to 1.0
    results: list[BenchmarkResult] = field(default_factory=list)
    drift_detected: bool = False

    @property
    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASS" if not self.drift_detected else "DRIFT DETECTED"
        return (
            f"Benchmark [{status}]: {self.passed}/{self.total_cases} "
            f"({self.score:.0%}) — agent '{self.agent_id}' at height {self.chain_height}"
        )


def run_benchmark(
    model: "Llama",
    agent_id: str = "",
    chain_height: int = 0,
    quality_floor: float = 0.6,
    cases: list[BenchmarkCase] | None = None,
) -> BenchmarkReport:
    """Run the semantic drift benchmark against a hydrated model.

    Tests core reasoning capabilities (logic, math, language, instruction
    following) that should be preserved regardless of how many epochs
    the agent has been through.

    Args:
        model: The hydrated Llama instance to test.
        agent_id: Agent identifier (for reporting).
        chain_height: Current chain height (for reporting).
        quality_floor: Minimum passing score (0.0–1.0). Below this
            threshold, drift is flagged. Default: 0.6 (4/6 core tests).
        cases: Optional custom benchmark cases. Defaults to CORE_BENCHMARKS.

    Returns:
        A BenchmarkReport with per-case results and drift detection.
    """
    cases = cases or CORE_BENCHMARKS
    results: list[BenchmarkResult] = []

    logger.info(
        "Running semantic drift benchmark (%d cases, floor=%.0f%%)...",
        len(cases),
        quality_floor * 100,
    )

    for case in cases:
        result = _run_single_case(model, case)
        results.append(result)

        status = "PASS" if result.passed else "FAIL"
        logger.debug(
            "  [%s] %s: %s — response: %s",
            status,
            case.category,
            case.description,
            result.response[:80].replace("\n", " "),
        )

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    score = passed / total if total > 0 else 0.0
    drift_detected = score < quality_floor

    report = BenchmarkReport(
        agent_id=agent_id,
        chain_height=chain_height,
        total_cases=total,
        passed=passed,
        failed=total - passed,
        score=score,
        results=results,
        drift_detected=drift_detected,
    )

    if drift_detected:
        logger.warning(
            "SEMANTIC DRIFT DETECTED: Score %.0f%% is below floor %.0f%%. "
            "The agent's core reasoning may be degraded after %d merges. "
            "Consider rolling back to a previous epoch.",
            score * 100,
            quality_floor * 100,
            chain_height,
        )
    else:
        logger.info("Benchmark passed: %s", report.summary)

    return report


def _run_single_case(model: "Llama", case: BenchmarkCase) -> BenchmarkResult:
    """Run a single benchmark case against the model."""
    try:
        response = model.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Answer concisely and accurately.",
                },
                {"role": "user", "content": case.prompt},
            ],
            max_tokens=256,
            temperature=0.1,  # Low temperature for deterministic answers
        )

        content = response["choices"][0]["message"]["content"].strip()

        # Check if any expected pattern matches
        passed = any(
            re.search(pattern, content) for pattern in case.expected_patterns
        )

        return BenchmarkResult(case=case, response=content, passed=passed)

    except Exception as e:
        logger.error("Benchmark case '%s' failed with error: %s", case.category, e)
        return BenchmarkResult(
            case=case,
            response="",
            passed=False,
            error=str(e),
        )
