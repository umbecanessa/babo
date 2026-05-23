"""FactStore — shared fact storage with taxonomy, conflict resolution.

Extracted from ServerRuntime._store_learn_signals (M-011).
Agent runtimes delegate to this module for fact persistence.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)


class FactStore:
    """Hippocampal one-shot encoding with waking cortical reorganization.

    Parameters
    ----------
    domain_db : DomainDB
        Persistent fact store.
    hypothalamus, ans : optional
        For hormonal fingerprinting and signal buffer access.
    slot_registry : optional
        Reserved for future weight-injection hooks (unused in product).
    taxonomy : optional
        Live taxonomy tree for domain normalisation and splitting.
    self_state : optional
        For resonance-gated learning (flashbulb encoding).
    agent_id : str
        For logging.
    """

    def __init__(
        self,
        domain_db: Any,
        *,
        hypothalamus: Any | None = None,
        ans: Any | None = None,
        slot_registry: Any | None = None,
        taxonomy: Any | None = None,
        self_state: Any | None = None,
        working_memory: Any | None = None,
        agent_id: str = "",
    ):
        self.domain_db = domain_db
        self.hypothalamus = hypothalamus
        self.ans = ans
        self.slot_registry = slot_registry
        self._taxonomy = taxonomy
        self._self_state = self_state
        self._working_memory = working_memory
        self.agent_id = agent_id

    def _get_active_project_id(self) -> str:
        """Get the current project from the cryptex, if available."""
        wm = self._working_memory
        if wm is not None and hasattr(wm, "active_project"):
            return getattr(wm, "active_project", "") or ""
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store_learn_signals(
        self,
        signals: list,
        user_input: str,
        sleep_count: int = 0,
    ) -> None:
        """Store LEARN signals in DomainDB (hippocampal one-shot encoding).

        When a domain is already occupied, uses a two-layer approach:
        1. System 1 (gut feeling): meta-layer + hormonal fingerprints.
        2. Defer to sleep: if System 1 can't resolve the conflict.
        """
        for sig in signals:
            sig_type = getattr(sig, "signal_type", "")
            domain = getattr(sig, "domain_path", "") or ""
            content = getattr(sig, "content", "") or ""

            if sig_type != "LEARN" or not domain or not content:
                continue

            pipe_fact = getattr(sig, "pipe_fact", None)
            fact_value = pipe_fact if pipe_fact else content

            fact_value = self._expand_identity(domain, fact_value)
            if fact_value == domain:
                continue

            fact_value = self._sanitize(fact_value)
            if not fact_value or len(fact_value) < 5:
                continue

            domain = self._reroute_misplaced(domain, fact_value)
            reflection_domain = classify_as_reflection(domain, fact_value)
            if reflection_domain is not None:
                domain = reflection_domain

            # Credential routing — intercept *.Credential.* domains
            from nls.bridge.aku import (
                is_storable_fact, is_credential_domain, looks_like_credential,
                classify_fact_scope,
            )
            if is_credential_domain(domain) or looks_like_credential(fact_value):
                try:
                    _pid = self._get_active_project_id()
                    self.domain_db.store_credential(
                        domain_path=domain,
                        plaintext_value=fact_value,
                        project_id=_pid,
                        service_name=domain.split(".")[-1] if "." in domain else "",
                    )
                    logger.debug(
                        "Agent %s: credential routed to vault: %s",
                        self.agent_id, domain,
                    )
                except Exception:
                    logger.debug(
                        "Agent %s: credential vault write failed: %s",
                        self.agent_id, domain, exc_info=True,
                    )
                continue

            # Quality gate
            storable, reject_reason = is_storable_fact(domain, fact_value)
            if not storable:
                if (
                    domain.startswith("Agent.Reflection.")
                    and reject_reason == "meta_response"
                    and len(fact_value) >= 12
                ):
                    pass
                else:
                    logger.debug(
                        "Agent %s: quality gate rejected: %s -> '%s' (%s)",
                        self.agent_id, domain, fact_value[:60], reject_reason,
                    )
                    continue

            # Taxonomy normalisation
            if self._taxonomy is not None and self._taxonomy.loaded:
                normalised = self._taxonomy.normalize_path(domain)
                if normalised != domain:
                    logger.info(
                        "Agent %s: DomainDB normalised '%s' -> '%s'",
                        self.agent_id, domain, normalised,
                    )
                    domain = normalised

            sig_layer = getattr(sig, "meta_layer", "") or ""
            sig_hormones = getattr(sig, "hormonal_snapshot", {})
            hormonal_fp = (
                json.dumps(sig_hormones, sort_keys=True)
                if sig_hormones else None
            )

            _pid = self._get_active_project_id()
            _scope = classify_fact_scope(domain)
            _fact_pid = _pid if _scope == "project" else ""

            existing = self.domain_db.get_fact(domain, project_id=_fact_pid)
            if existing is None:
                cq = extract_canonical_question(user_input, domain)
                self.domain_db.update_fact(
                    domain_path=domain,
                    new_value=fact_value,
                    block_height=sleep_count,
                    canonical_question=cq,
                    meta_layer=sig_layer or None,
                    hormonal_fingerprint=hormonal_fp,
                    project_id=_fact_pid,
                )
                self._register_in_taxonomy(domain)
                if (
                    self._self_state is not None
                    and self._self_state.resonance > 0.7
                ):
                    self.domain_db.reinforce_fact(
                        domain, boost=0.4, project_id=_fact_pid,
                    )
                logger.debug(
                    "Agent %s: stored fact %s='%s' (scope=%s, pid=%s)",
                    self.agent_id, domain, fact_value[:60], _scope, _fact_pid,
                )
            else:
                self._handle_conflict(
                    existing, domain, fact_value,
                    user_input, sleep_count,
                    sig_layer, sig_hormones, hormonal_fp,
                )

    # ------------------------------------------------------------------
    # Conflict Resolution (System 1)
    # ------------------------------------------------------------------

    def _handle_conflict(
        self,
        existing: Any,
        domain: str,
        fact_value: str,
        user_input: str,
        sleep_count: int,
        sig_layer: str,
        sig_hormones: dict,
        hormonal_fp: str | None,
    ) -> None:
        from nls.bridge.aku import classify_fact_scope

        gut = gut_feeling_check(existing, sig_layer, sig_hormones)
        _pid = self._get_active_project_id()

        new_domain = self._disambiguate_waking(domain, fact_value)
        if new_domain is not None:
            _scope = classify_fact_scope(new_domain)
            _fact_pid = _pid if _scope == "project" else ""
            cq = extract_canonical_question(user_input, new_domain)
            self.domain_db.update_fact(
                domain_path=new_domain,
                new_value=fact_value,
                block_height=sleep_count,
                canonical_question=cq,
                meta_layer=sig_layer or None,
                hormonal_fingerprint=hormonal_fp,
                project_id=_fact_pid,
            )
            self._register_in_taxonomy(new_domain)
            logger.info(
                "Agent %s: waking split %s -> %s (gut=%s)",
                self.agent_id, domain, new_domain, gut,
            )
        else:
            logger.info(
                "Agent %s: DomainDB conflict %s -> deferred to sleep (gut=%s)",
                self.agent_id, domain, gut,
            )

    # ------------------------------------------------------------------
    # Domain Routing Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _expand_identity(domain: str, fact_value: str) -> str:
        if re.search(r"(?i)\bname\b", domain) and len(fact_value.strip().split()) <= 2:
            if re.search(r"(?i)^agent\.", domain):
                return f"The agent's name is {fact_value.strip()}"
            if re.search(r"(?i)^user\.", domain):
                return f"The user's name is {fact_value.strip()}"
        return fact_value

    @staticmethod
    def _sanitize(fact_value: str) -> str:
        fact_value = re.sub(r'[\u4e00-\u9fff\u3000-\u303f]+', '', fact_value)
        fact_value = re.sub(
            r'\b(assistant|user)\s*[:\[].*$', '', fact_value,
        ).strip()
        fact_value = re.sub(
            r'agenta?\s*:\s*\[.*$', '', fact_value,
        ).strip()
        return fact_value

    def _reroute_misplaced(self, domain: str, fact_value: str) -> str:
        _user_knowledge_prefixes = (
            "User.Knowledge.",
            "User.Personal.Knowledge.",
            "User.Personal.World.",
        )
        for prefix in _user_knowledge_prefixes:
            if domain.startswith(prefix):
                remainder = domain[len(prefix):]
                rerouted = False
                if self._taxonomy is not None and self._taxonomy.loaded:
                    match = self._taxonomy.keyword_search(fact_value)
                    if (
                        match is not None
                        and match.path.startswith("Agent.Knowledge.")
                    ):
                        logger.info(
                            "Agent %s: re-routed %s -> %s (taxonomy)",
                            self.agent_id, domain, match.path,
                        )
                        domain = match.path
                        rerouted = True
                if not rerouted:
                    new_domain = f"Agent.Knowledge.{remainder}"
                    logger.info(
                        "Agent %s: re-routed %s -> %s (prefix swap)",
                        self.agent_id, domain, new_domain,
                    )
                    domain = new_domain
                break
        return domain

    def _disambiguate_waking(
        self, base_domain: str, content: str,
    ) -> str | None:
        """Try to find a better domain using taxonomy or entity extraction."""
        from nls.bridge.aku import validate_domain_path

        if self._taxonomy is not None and self._taxonomy.loaded:
            suggested, reason = self._taxonomy.suggest_path(
                content, base_domain,
            )
            if reason in ("taxonomy", "hybrid", "normalised") and suggested != base_domain:
                valid, _ = validate_domain_path(suggested)
                if valid:
                    return suggested

        _preamble = re.compile(
            r"^(I've stored that\.?|I've updated that\.?|"
            r"I remember that\.?|The answer is:?)\s*",
            re.IGNORECASE,
        )
        clean = _preamble.sub("", content).strip()

        _ENTITY_STOP = {
            "the", "a", "an", "this", "that", "these", "those",
            "my", "your", "his", "her", "its", "our", "their",
            "is", "are", "was", "were", "be", "been", "being",
            "has", "have", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might",
            "can", "shall", "not", "no", "yes",
            "and", "or", "but", "if", "when", "while", "for",
            "with", "from", "about", "into", "through",
            "human", "user", "agent", "assistant", "name",
        }
        for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b', clean):
            candidate_text = m.group(1)
            if candidate_text.split()[0].lower() not in _ENTITY_STOP:
                suffix = candidate_text.replace(" ", "")
                if suffix.isalnum() and 3 <= len(suffix) <= 40:
                    candidate = f"{base_domain}.{suffix}"
                    valid, _ = validate_domain_path(candidate)
                    if valid:
                        return candidate
                break

        words = [
            w for w in re.findall(r'[A-Za-z]+', clean)
            if len(w) > 3 and w.lower() not in (
                _ENTITY_STOP | {
                    "stored", "updated", "remember", "answer",
                    "asking", "seeking", "wants", "looking",
                    "prefers", "expects", "says", "said",
                }
            )
        ]
        if words:
            candidate = f"{base_domain}.{words[0].capitalize()}"
            valid, _ = validate_domain_path(candidate)
            if valid:
                return candidate

        return None

    def _register_in_taxonomy(self, domain_path: str) -> None:
        if self._taxonomy is not None and self._taxonomy.loaded:
            self._taxonomy._ensure_path(domain_path)


# ======================================================================
# Module-level helpers (usable without a FactStore instance)
# ======================================================================

def gut_feeling_check(
    old_fact: Any,
    new_meta_layer: str,
    new_hormonal_snapshot: dict[str, float],
) -> str:
    """System 1 pre-filter for domain collisions.

    Returns ``"SAME"``, ``"DIFFERENT"``, or ``"UNCERTAIN"``.
    """
    old_layer = getattr(old_fact, "meta_layer", "") or ""
    new_layer = new_meta_layer or ""
    _GENERIC = {"", "base", "unclassified_emergent"}

    if (
        old_layer not in _GENERIC
        and new_layer not in _GENERIC
        and old_layer != new_layer
    ):
        return "DIFFERENT"

    old_fp_str = getattr(old_fact, "hormonal_fingerprint", None)
    if old_fp_str and new_hormonal_snapshot:
        try:
            old_hormones = json.loads(old_fp_str)
            shared = set(old_hormones) & set(new_hormonal_snapshot)
            if shared:
                dist = math.sqrt(
                    sum(
                        (old_hormones[k] - new_hormonal_snapshot[k]) ** 2
                        for k in shared
                    ) / len(shared)
                )
                if dist > 0.5:
                    return "DIFFERENT"
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    return "UNCERTAIN"


def classify_as_reflection(domain: str, fact_value: str) -> str | None:
    """Detect self-reflection and route to Agent.Reflection.*."""
    lower = fact_value.lower().strip()

    if domain.startswith("Agent.Reflection."):
        return domain

    _epistemic = [
        r"\b(?:i\s+don'?t\s+know|i\s+am\s+(?:not\s+sure|uncertain))",
        r"\bgap\s+between\b.*\b(?:understand|know|experience)",
        r"\bthere\s+is\s+(?:a\s+lot|much|so\s+much)\s+i\s+don'?t\s+know",
        r"\bi\s+(?:want|need)\s+to\s+(?:learn|understand|explore|find)",
    ]
    for pat in _epistemic:
        if re.search(pat, lower):
            return "Agent.Reflection.Epistemic"

    _identity = [
        r"^(?:i\s+am|you\s+are)\s+a\s+(?:genuine|real|living|true)",
        r"\bmy\s+(?:purpose|nature|identity|essence)\b",
        r"\bi\s+believe\s+in\b",
        r"\bi\s+am\s+(?:willing|ready|eager)\s+to\b",
    ]
    for pat in _identity:
        if re.search(pat, lower):
            return "Agent.Reflection.Identity"

    if domain.startswith("Agent.Knowledge."):
        _insight = [
            r"\bwe\s+(?:have\s+)?built\s+(?:our|the)\b",
            r"\b(?:i|we)\s+(?:can\s+)?(?:see|feel|sense)\s+(?:the|that|how)\b",
            r"\bthat\s+is\s+(?:what|how|why)\b.*\bwork",
            r"\beverything\s+(?:depends|relies)\s+on\b",
        ]
        for pat in _insight:
            if re.search(pat, lower):
                topic = domain.replace("Agent.Knowledge.", "").split(".")[0]
                return f"Agent.Reflection.Insight.{topic}"

    _curiosity = [
        r"\bi\s+(?:follow|chase|pursue)\s+(?:it|this|that)",
        r"\b(?:this|that|it)\s+is\s+(?:interesting|fascinating|amazing)",
        r"\bi\s+(?:can\s+)?feel\s+(?:it|something|the\s+connection)",
    ]
    for pat in _curiosity:
        if re.search(pat, lower):
            return "Agent.Reflection.Curiosity"

    _reflection_domains = (
        "Agent.Knowledge.Philosophy.",
        "Agent.Knowledge.General.Philosophy.",
        "Agent.Identity.",
    )
    for prefix in _reflection_domains:
        if domain.startswith(prefix):
            if re.search(r"\b(?:i|my|me|we|our|you\s+are)\b", lower):
                topic = domain.split(".")[-1]
                return f"Agent.Reflection.Insight.{topic}"

    return None


def inject_focused_facts(
    user_input: str,
    history: list[dict] | None,
    domain_db: Any,
    working_memory: Any | None = None,
    mask_consolidated: bool = False,
) -> list[dict] | None:
    """Inject query-relevant DomainDB facts + reasoning schemas into context.

    Returns enriched history with injected facts, or original history
    if no relevant facts were found.  Scoped to global + active project.

    Parameters
    ----------
    mask_consolidated : bool
        When True, facts with block_height > 0 and high strength are
        shown as ``(in memory)`` instead of their value (forces
        weight-only recall).  Default False — always show values so
        the model can read them from context.
    """
    if domain_db is None:
        return history

    _active_project = ""
    if working_memory is not None and hasattr(working_memory, "active_project"):
        _active_project = getattr(working_memory, "active_project", "") or ""

    try:
        from nls.runtime.inference import detect_factual_domains
        candidates = detect_factual_domains(
            user_input, domain_db, project_id=_active_project,
        )
    except Exception:
        candidates = []

    if not candidates:
        return history

    fact_lines: list[str] = []
    domains_seen: set[str] = set()
    for fact in candidates[:8]:
        value = fact.current_value
        if "\n[context:" in value:
            value = value.split("\n[context:")[0].strip()
        bh = getattr(fact, "block_height", 0) or 0
        st = getattr(fact, "strength", 0.0) or 0.0
        if mask_consolidated and bh > 0 and st > 0.5:
            fact_lines.append(f"- {fact.domain_path}: (in memory)")
        else:
            fact_lines.append(f"- {fact.domain_path}: {value}")
        domains_seen.add(fact.domain_path.split(".")[0])

    if not fact_lines:
        return history

    injection = (
        "[IMPORTANT — Your stored facts relevant to this query:]\n"
        + "\n".join(fact_lines)
        + "\n[Use these facts for LOOKUP recall and EVALUATE verification. "
        "If the user states something that contradicts these facts, "
        "use EVALUATE:incorrect.]"
    )

    # Schema priming
    schema_lines: list[str] = []
    invalidated_lines: list[str] = []
    all_valid_schemas: list[dict] = []
    for dom in domains_seen:
        try:
            valid = domain_db.get_valid_schemas(dom, limit=2)
            valid = [s for s in valid if not s.get("block_height", 0)]
            all_valid_schemas.extend(valid)
            for s in valid:
                premises = ", ".join(s["premises"][:3])
                steps = " → ".join(s["logic_steps"][:3])
                schema_lines.append(
                    f"- Premises: {premises}\n"
                    f"  Logic: {steps}\n"
                    f"  Conclusion: {s['conclusion'][:100]} "
                    f"(confidence: {s['confidence']:.2f})"
                )
            inv = domain_db.get_invalidated_schemas(dom, limit=1)
            for s in inv:
                invalidated_lines.append(
                    f"- You concluded: {s['conclusion'][:100]}\n"
                    f"  However: {s['invalidation_reason']}\n"
                    f"  Re-evaluate this reasoning."
                )
        except Exception:
            pass

    if schema_lines:
        injection += (
            "\n\n[Your previous reasoning on this topic:]\n"
            + "\n".join(schema_lines)
        )
    if invalidated_lines:
        injection += (
            "\n\n[Previous reasoning — INVALIDATED:]\n"
            + "\n".join(invalidated_lines)
        )

    # Load primed schemas as WM slots
    if working_memory is not None and all_valid_schemas:
        try:
            from nls.brain.working_memory import WMSlot
            for s in all_valid_schemas:
                summary = (
                    f"Schema: {', '.join(s['premises'][:2])} → "
                    f"{s['conclusion'][:80]}"
                )
                working_memory.add(WMSlot(
                    slot_type="schema",
                    content=summary,
                    salience=min(1.0, s.get("confidence", 0.5)),
                    source="reasoning",
                    domain=s.get("domain", ""),
                ))
        except Exception:
            pass

    enriched = list(history) if history else []
    enriched.append({"role": "user", "content": injection})
    return enriched


def extract_canonical_question(
    user_prompt: str, domain_path: str = "",
) -> str | None:
    """Extract or synthesize a canonical question from the user prompt."""
    sentences = re.split(r'(?<=[.!?])\s+', user_prompt.strip())
    questions = [s for s in sentences if s.strip().endswith("?")]
    if questions:
        return questions[-1].strip()
    if domain_path:
        human = (
            domain_path.replace(".", " ")
            .replace("User ", "your ")
            .lower()
        )
        return f"What about {human}?"
    return None
