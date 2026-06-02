"""NLS Thinking Handler — Strip, assess, and preserve <think> chains.

Qwen3 (and similar reasoning models) produce a `<think>...</think>` chain
before the visible answer.  This module provides utilities to:

  1. Extract and separate the thinking chain from the final answer.
  2. Assess coherence of the reasoning chain (ACC metacognition).
  3. Detect self-correction patterns (serotonin triggers).
  4. Log the thinking at DEBUG level for observability.
  5. Return the clean answer for downstream processing.

The thinking chain is valuable data — it reveals the model's reasoning
process.  We never discard it; we just route it to the right place
(logs, debug panels, coherence assessment) instead of leaking it into
user-facing output.

Usage:
    from nls.brain.thinking import strip_thinking, assess_coherence

    raw = model_decode(...)
    answer, thinking = strip_thinking(raw)
    if thinking:
        score = assess_coherence(thinking, answer)
        has_correction = detect_self_correction(thinking)
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Regex to match <think>...</think> blocks (including nested newlines)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# Phase 11h: with enable_thinking=false, Qwen3.5 sometimes emits
# plain-text "Thinking Process:" blocks as visible content.
_PLAINTEXT_THINK_RE = re.compile(
    r"^(?:\*\*)?Thinking Process(?:\*\*)?:?\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Patterns for coherence assessment
_CORRECTION_PATTERNS = re.compile(
    r"\b(wait|actually|no,? that'?s wrong|let me reconsider|"
    r"I made an? (?:error|mistake)|correction|on second thought|"
    r"that'?s not right|let me rethink|I was wrong|"
    r"hmm,? (?:actually|no)|re-evaluating)\b",
    re.IGNORECASE,
)

_CONCLUSION_PATTERNS = re.compile(
    r"\b(therefore|thus|so the answer|in conclusion|"
    r"the (?:answer|result|solution) is|finally|"
    r"to summarize|in summary|hence)\b",
    re.IGNORECASE,
)

_CONTRADICTION_PATTERNS = re.compile(
    r"\b(but (?:also|then again|wait)|however|on the other hand|"
    r"contradicts? what|this conflicts?|"
    r"that can'?t be (?:right|correct))\b",
    re.IGNORECASE,
)


def strip_thinking(raw_response: str) -> tuple[str, str]:
    """Separate thinking chain from the visible answer.

    Args:
        raw_response: The full model output (may contain <think>...</think>).

    Returns:
        (answer, thinking) — both stripped of leading/trailing whitespace.
        If no thinking block is found, thinking is "".
    """
    if not raw_response:
        return "", ""

    match = _THINK_RE.search(raw_response)
    if match:
        thinking = match.group(1).strip()
        answer = raw_response[match.end():].strip()
    else:
        # Phase 11h: strip plain-text "Thinking Process:" blocks
        pt_match = _PLAINTEXT_THINK_RE.search(raw_response)
        if pt_match:
            thinking = pt_match.group(1).strip()
            answer = raw_response[pt_match.end():].strip()
        else:
            return raw_response.strip(), ""

    if thinking:
        think_words = len(thinking.split())
        logger.debug(
            "Thinking chain: %d words, preview: %s",
            think_words,
            thinking[:120].replace("\n", " "),
        )

    return answer, thinking


def assess_coherence(thinking: str, answer: str = "") -> float:
    """Assess the coherence of a thinking chain.

    Brain analog: The anterior cingulate cortex (ACC) monitors cognitive
    conflict.  When reasoning contradicts itself, you feel "something is
    off."  This is metacognition about reasoning quality.

    Checks:
    - **Circular detection**: repeated phrases suggest going in circles
    - **Contradiction detection**: internal conflicts in the chain
    - **Resolution detection**: does the chain reach a clear conclusion?
    - **Length ratio**: extreme thinking:answer ratios suggest struggle

    Returns a score from 0.0 (incoherent) to 1.0 (clean reasoning).
    """
    if not thinking:
        return 1.0  # No thinking = no incoherence

    score = 1.0
    words = thinking.split()
    thinking_words = len(words)

    # 1. Circular detection — repeated phrases (trigrams)
    if thinking_words > 20:
        trigrams = [
            " ".join(words[i:i + 3]).lower()
            for i in range(len(words) - 2)
        ]
        unique_ratio = len(set(trigrams)) / len(trigrams) if trigrams else 1.0
        if unique_ratio < 0.5:
            score -= 0.3  # Heavy repetition
        elif unique_ratio < 0.7:
            score -= 0.15  # Moderate repetition

    # 2. Contradiction detection
    contradictions = len(_CONTRADICTION_PATTERNS.findall(thinking))
    if contradictions > 3:
        score -= 0.25
    elif contradictions > 1:
        score -= 0.1

    # 3. Resolution detection — does it reach a conclusion?
    has_conclusion = bool(_CONCLUSION_PATTERNS.search(thinking))
    if not has_conclusion and thinking_words > 100:
        score -= 0.15  # Long chain with no resolution

    # 4. Length ratio — extreme ratios suggest the model struggled
    answer_words = len(answer.split()) if answer else 1
    ratio = thinking_words / max(answer_words, 1)
    if ratio > 30:
        score -= 0.2  # Extremely long thinking relative to answer
    elif ratio > 20:
        score -= 0.1

    # 5. Self-correction bonus — model catching its own errors is healthy
    corrections = len(_CORRECTION_PATTERNS.findall(thinking))
    if 1 <= corrections <= 3:
        score += 0.05  # Healthy self-correction
    elif corrections > 5:
        score -= 0.1  # Too many corrections suggests confusion

    return max(0.0, min(1.0, score))


def detect_self_correction(thinking: str) -> bool:
    """Detect if the thinking chain contains self-correction.

    Brain analog: When the model catches its own error mid-reasoning,
    this mirrors healthy metacognition (serotonin pathway).

    Returns True if the chain shows evidence of self-correction.
    """
    if not thinking:
        return False
    return bool(_CORRECTION_PATTERNS.search(thinking))


# -----------------------------------------------------------------------
# Reasoning trajectory extraction (for cross-iteration continuity)
# -----------------------------------------------------------------------

_TRAJECTORY_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?:So |Okay,? )?(?:the plan|my plan|next|now) (?:is|I (?:need|should|will|'ll))"
    r"|I'?ll (?:click|type|navigate|open|search|read|write|run|call|check|verify)"
    r"|(?:Step \d|Next step|Moving on|Let me)"
    r"|(?:The (?:result|output|response|page|error) (?:shows?|says?|indicates?|is))"
    r"|(?:This (?:means|suggests|confirms|worked|failed))"
    r")",
    re.IGNORECASE,
)


def extract_trajectory(thinking: str, max_chars: int = 600) -> str:
    """Extract the reasoning trajectory from a thinking chain.

    Takes the TAIL of the thinking — the part closest to the
    conclusion/decision.  This is the model's "where I am right now"
    state, most relevant for continuation.

    When possible, splits at a structural marker (plan statement,
    decision point) rather than a hard character boundary so the
    continuation reads naturally.

    Zero-cost: string operations only, no LLM call.
    """
    if not thinking:
        return ""

    thinking = thinking.strip()
    if len(thinking) <= max_chars:
        return thinking

    tail = thinking[-max_chars:]

    best_pos = -1
    for m in _TRAJECTORY_MARKERS.finditer(tail):
        best_pos = m.start()
        break  # first marker in the tail = closest to the split point

    if best_pos > 0:
        tail = tail[best_pos:].lstrip()
    else:
        nl = tail.find("\n")
        if 0 < nl < 80:
            tail = tail[nl + 1:].lstrip()

    return tail
