"""NLS Reasoning Distiller — Hippocampal schema formation from thinking chains.

Brain analog: The hippocampus doesn't store every millisecond of experience.
It distills episodes into structured schemas: premises, causal links,
conclusions.  This converts episodic memory (the raw thinking chain) into
semantic memory (a reusable reasoning schema).

The distiller takes a raw ``<think>`` chain and extracts the crystallized
reasoning path — premises, logical steps, and conclusion — using a short,
cheap model inference (no thinking, max 200 tokens).

Usage:
    from nls.knowledge.reasoning import ReasoningDistiller, ReasoningSchema

    distiller = ReasoningDistiller(model, tokenizer, config)
    schema = distiller.distill(thinking_chain, answer, user_input)
    if schema:
        domain_db.store_schema(schema)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import torch
except ImportError:
    torch = None  # type: ignore[assignment]  # Desktop mode (vLLM only)

logger = logging.getLogger(__name__)


@dataclass
class ReasoningSchema:
    """A distilled reasoning pattern — the crystallized path from premises
    to conclusion, extracted from a model's thinking chain.

    This is the NLS equivalent of a hippocampal schema: a reusable
    reasoning structure that can be primed, trained on, invalidated,
    and used for creative hypothetical replay.
    """

    premises: list[str]           # Key facts used as inputs
    logic_steps: list[str]        # The crystallized reasoning path
    conclusion: str               # Final answer/judgment
    domain: str                   # Primary domain path
    confidence: float = 0.5       # 0.0-1.0 self-assessed
    source_turn: int = 0          # Which turn produced this
    thinking_words: int = 0       # Length of raw chain
    coherence_score: float = 0.0  # Logic consistency (Phase 6)
    invalidated: bool = False     # Set by Neural Eraser cascade
    invalidation_reason: str = ""
    schema_id: int | None = None  # DB primary key (set after storage)


class ReasoningDistiller:
    """Extracts structured reasoning schemas from thinking chains.

    Uses a short, cheap model inference (max 200 tokens, no thinking)
    to ask the model to distill its own reasoning.  The raw thinking
    chain is NOT stored — only the distilled schema persists.
    """

    # Extraction prompt template
    _EXTRACT_PROMPT = (
        "Extract the key reasoning structure from the following thinking chain. "
        "Return ONLY valid JSON with these fields:\n"
        '- "premises": list of key facts or assumptions used\n'
        '- "logic_steps": list of logical steps taken\n'
        '- "conclusion": the final answer/judgment\n'
        '- "domain": the primary knowledge domain (e.g. "Science.Physics")\n'
        '- "confidence": 0.0-1.0 how confident the reasoning seems\n\n'
        "Thinking chain:\n{thinking}\n\n"
        "Answer given:\n{answer}\n\n"
        "JSON:"
    )

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        config: dict | None = None,
    ):
        self.model = model
        self.tokenizer = tokenizer
        self.config = config or {}
        self._min_words = self.config.get("thinking", {}).get(
            "min_distill_words", 50,
        )

    def distill(
        self,
        thinking_chain: str,
        answer: str,
        user_input: str,
        *,
        source_turn: int = 0,
        narrative_coherence: float = 0.7,
    ) -> ReasoningSchema | None:
        """Distill a thinking chain into a structured reasoning schema.

        Returns None if:
        - The thinking chain is too short (< min_distill_words)
        - The extraction inference fails
        - The JSON cannot be parsed

        Parameters
        ----------
        thinking_chain : str
            The raw ``<think>`` content (already stripped of tags).
        answer : str
            The model's visible answer.
        user_input : str
            The user's original input (for context).
        source_turn : int
            The conversation turn number.
        narrative_coherence : float
            Current narrative coherence at distillation time (IR-8.5).
            Schemas formed during high coherence get a confidence bonus.
        """
        if not thinking_chain:
            return None

        thinking_words = len(thinking_chain.split())
        if thinking_words < self._min_words:
            logger.debug(
                "Thinking chain too short for distillation (%d words < %d)",
                thinking_words,
                self._min_words,
            )
            return None

        # Build extraction prompt
        # Truncate very long chains to keep extraction fast
        truncated = thinking_chain[:3000] if len(thinking_chain) > 3000 else thinking_chain
        prompt = self._EXTRACT_PROMPT.format(
            thinking=truncated,
            answer=answer[:500],
        )

        try:
            extracted = self._extract(prompt)
        except Exception as exc:
            logger.warning("Reasoning distillation failed: %s", exc)
            return None

        if extracted is None:
            return None

        raw_confidence = max(0.0, min(1.0, float(extracted.get("confidence", 0.5))))
        # IR-8.5: Narrative-weighted coherence bonus
        coherence_multiplier = 0.8 + 0.4 * narrative_coherence
        adjusted_confidence = max(0.0, min(1.0, raw_confidence * coherence_multiplier))

        schema = ReasoningSchema(
            premises=extracted.get("premises", []),
            logic_steps=extracted.get("logic_steps", []),
            conclusion=extracted.get("conclusion", answer[:200]),
            domain=extracted.get("domain", "General"),
            confidence=adjusted_confidence,
            source_turn=source_turn,
            thinking_words=thinking_words,
            coherence_score=narrative_coherence,
        )

        logger.info(
            "Distilled reasoning schema: domain=%s, %d premises, "
            "%d logic steps, confidence=%.2f",
            schema.domain,
            len(schema.premises),
            len(schema.logic_steps),
            schema.confidence,
        )

        return schema

    def _extract(self, prompt: str) -> dict | None:
        """Run a short, cheap inference to extract reasoning structure.

        Uses /no_think to suppress nested thinking chains and limits
        output to 200 tokens for speed.
        """
        # Prepend /no_think to avoid recursive thinking
        messages = [
            {"role": "system", "content": "You are a reasoning analyzer. Output only valid JSON."},
            {"role": "user", "content": "/no_think\n" + prompt},
        ]

        try:
            input_text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
        except Exception:
            input_text = f"### Instruction:\n{prompt}\n\n### Response:\n"

        inputs = self.tokenizer(input_text, return_tensors="pt").to(self.model.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=200,
                do_sample=False,  # greedy for determinism
                temperature=1.0,
                pad_token_id=self.tokenizer.pad_token_id,
            )

        generated = outputs[0][inputs["input_ids"].shape[-1]:]
        raw_text = self.tokenizer.decode(generated, skip_special_tokens=True).strip()

        # Strip any accidental thinking tags
        from nls.brain.thinking import strip_thinking
        clean_text, _ = strip_thinking(raw_text)

        # Parse JSON from the response
        return self._parse_json(clean_text)

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Robustly parse JSON from model output."""
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON block in the text
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Try to find ```json blocks
        code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        logger.debug("Could not parse JSON from distillation output: %s", text[:200])
        return None
