"""NLS AKU — Atomic Knowledge Unit parsing, validation, and taxonomy tagging.

Validates AKU schema, enforces hierarchical dot-notation taxonomy,
and deduplicates against the domain database.
"""

from __future__ import annotations

import json
import logging
import re

from nls.ledger.domain_db import DomainDB
from nls.models import AKU, SyntheticPair

logger = logging.getLogger(__name__)

# Valid top-level taxonomy prefixes
# Top-level prefixes are NOT restricted.  The agent creates its own
# domain taxonomy organically -- just like the brain grows new neural
# pathways.  The thalamus handles routing strength (weak for new
# domains, strong for established ones); we only validate FORMAT here
# (dot-notation, no garbage characters).
#
# Common prefixes observed: User, Agent, Base, Project, System, Domain
VALID_PREFIXES: set[str] | None = None  # None = accept any prefix

# Minimum depth for a domain path (e.g., "User.Tech" = depth 2)
MIN_DOMAIN_DEPTH = 2

# Regex for valid domain path segments: alphanumeric + underscores
_SEGMENT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def validate_domain_path(path: str) -> tuple[bool, str]:
    """Validate a hierarchical dot-notation domain path.

    Valid:   "User.Tech.Framework.Frontend"
    Invalid: "User", "user.tech", "User..Tech", "User.123"

    Returns (is_valid, error_message).
    """
    if not path:
        return False, "Domain path is empty."

    segments = path.split(".")

    if len(segments) < MIN_DOMAIN_DEPTH:
        return False, f"Domain path must have at least {MIN_DOMAIN_DEPTH} segments: '{path}'"

    # Check top-level prefix (only if a whitelist is configured)
    if VALID_PREFIXES is not None and segments[0] not in VALID_PREFIXES:
        return (
            False,
            f"Invalid top-level prefix '{segments[0]}'. "
            f"Must be one of: {', '.join(sorted(VALID_PREFIXES))}",
        )

    # Check each segment
    for i, seg in enumerate(segments):
        if not seg:
            return False, f"Empty segment at position {i} in '{path}'"
        if not _SEGMENT_PATTERN.match(seg):
            return (
                False,
                f"Invalid segment '{seg}' at position {i}. "
                "Segments must start with a letter and contain only alphanumeric/underscores.",
            )

    return True, ""


# ── Fact quality gate (prefrontal filter) ─────────────────────────────
#
# Not every LEARN signal should be stored.  Just like the prefrontal
# cortex filters irrelevant thoughts before they reach long-term memory,
# this gate rejects garbage before it enters DomainDB or consolidation.

_NUMERIC_ONLY_RE = re.compile(r"^[\d.:,\s]+$")
_META_CONTENT_TERMS = frozenset({
    "learn signal", "learn tag", "signal tag", "signal system",
    "stored fact", "trained fact", "weight train", "emit",
    "mental map", "domain path",
    "stored in my", "trained into my", "in my weights",
    "i have it trained", "that is stored", "goes into my",
    "noted in my", "saved in my", "recorded in my",
    "registration date", "serial number", "tracking number",
    "order number", "invoice number", "reference number",
    # NLS internal artefacts the model may hallucinate
    "validation key", "nls key", "nls validation",
    "parameter on a mind",
    # Meta-process noise (agent narrating its own cognition)
    "honest signal", "internal state says nothing",
})
_SIGNAL_LEAKAGE_RE = re.compile(
    r"(?:\d+-)?(?:ACC|EVALUATE|LEARN|LOOKUP|REFLECT|CONNECT|DOUBT|VALUES)"
    r"[:\-.]",
    re.IGNORECASE,
)
# Pure meta-responses: agent talks about remembering/connecting but
# provides no factual content.  These have ZERO educational value.
# Pattern: sentence starts with "i recall/connect/store/remember that"
# and the remainder contains no concrete nouns or data — just filler.
_META_RESPONSE_RE = re.compile(
    r"^(?:"
    r"(?:i\s+(?:recall|remember|connect|store|have)\s+"
    r"(?:that|this|it)[\s.,]*)"
    r"|(?:that\s+is\s+(?:stored|saved|noted|recorded|trained|in\s+my))"
    r"|(?:i\s+(?:have|got)\s+(?:it|this|that)\s+"
    r"(?:stored|saved|trained|in\s+my))"
    r"|(?:(?:stored|noted|recorded|saved)\s+in\s+my)"
    r"|(?:this\s+(?:is|goes)\s+(?:in|into)\s+my)"
    r")"
    r"[\s.,]*"
    r"(?:it\s+is\s+|this\s+is\s+|i\s+have\s+it\s+)?"
    r"(?:one\s+of\s+)?(?:the\s+)?(?:most\s+)?"
    r"(?:important|significant|meaningful|essential|trained|weight|memor|biology|knowledge|mental)?"
    r".*$",
    re.IGNORECASE,
)

_STORABLE_PREFIX_WHITELIST = {
    "Agent", "User", "Base", "Project", "System", "Domain", "Dream",
    "Social", "Account",
}

_GLOBAL_PREFIXES = {"User", "Agent", "System", "Social", "Dream"}
_DOMAIN_PREFIXES = {"Domain", "Base"}

_CREDENTIAL_PATTERNS = [
    re.compile(r"(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?:password|passwd|pwd)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"(?:mongodb\+srv|postgresql|mysql|redis)://\S+:\S+@"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


def classify_fact_scope(domain_path: str) -> str:
    """Deterministically classify a domain path into a scope layer.

    Returns ``'global'``, ``'project'``, or ``'domain'`` based on the
    top-level prefix, mirroring the Cryptex ring categories.
    """
    if not domain_path:
        return "global"
    top = domain_path.split(".")[0]
    if top in _GLOBAL_PREFIXES:
        return "global"
    if top == "Project":
        return "project"
    if top in _DOMAIN_PREFIXES:
        return "domain"
    return "global"


def looks_like_credential(value: str) -> bool:
    """Return True if *value* matches a known credential/secret pattern."""
    for pat in _CREDENTIAL_PATTERNS:
        if pat.search(value):
            return True
    return False


def is_credential_domain(domain_path: str) -> bool:
    """Return True if *domain_path* belongs to the credential namespace."""
    return ".Credential." in domain_path or ".Credentials." in domain_path


def is_storable_fact(
    domain_path: str,
    fact_value: str,
) -> tuple[bool, str]:
    """Check whether a fact is worth storing in DomainDB / sleep consolidation.

    Returns ``(True, "")`` if the fact passes all checks, or
    ``(False, reason)`` if it should be rejected.

    Checks (ordered cheapest first):

    1. Minimum meaningful length (>= 12 chars)
    2. No numeric-only content
    3. No self-referential meta content (agent talking about signals)
    4. Domain prefix whitelist
    5. No signal-tag leakage in the value
    6. No pure meta-response
    7. Credential leak detection — secrets outside credential domains
    """
    stripped = fact_value.strip() if fact_value else ""

    # 1. Minimum length — rejects "123", "correct", "a fact"
    if len(stripped) < 12:
        return False, f"too_short ({len(stripped)} chars)"

    # 2. Numeric-only — rejects "123", "16S::1.2", "127.6:1.3:"
    if _NUMERIC_ONLY_RE.match(stripped):
        return False, "numeric_only"

    # 3. Meta-content — agent talking about how signals work
    lower = stripped.lower()
    for term in _META_CONTENT_TERMS:
        if term in lower:
            return False, f"meta_content ({term})"

    # 4. Domain prefix whitelist
    if domain_path:
        top_level = domain_path.split(".")[0]
        if top_level not in _STORABLE_PREFIX_WHITELIST:
            return False, f"bad_prefix ({top_level})"

    # 5. Signal leakage in the value
    if _SIGNAL_LEAKAGE_RE.search(stripped):
        return False, "signal_leakage"

    # 6. Pure meta-response — agent narrating its own recall/processing
    if _META_RESPONSE_RE.match(lower):
        return False, "meta_response"

    # 7. Credential leak detection — reject secrets outside *.Credential.* domains
    if looks_like_credential(stripped) and not is_credential_domain(domain_path):
        return False, "credential_leak"

    return True, ""


def validate_aku(aku: AKU) -> tuple[bool, list[str]]:
    """Validate an AKU's structure and content.

    Checks:
    - Domain path is valid.
    - Fact and logic_change are non-empty.
    - At least 1 synthetic pair exists.
    - Each synthetic pair has non-empty instruction and output.
    - Confidence is in range [0, 1].

    Returns (is_valid, list_of_errors).
    """
    errors: list[str] = []

    # Domain path
    valid, msg = validate_domain_path(aku.domain_path)
    if not valid:
        errors.append(f"domain_path: {msg}")

    # Content checks
    if not aku.fact.strip():
        errors.append("fact: Must not be empty.")
    if not aku.logic_change.strip():
        errors.append("logic_change: Must not be empty.")

    # Synthetic pairs
    if not aku.synthetic_pairs:
        errors.append("synthetic_pairs: Must have at least 1 pair.")
    else:
        for i, pair in enumerate(aku.synthetic_pairs):
            if not pair.instruction.strip():
                errors.append(f"synthetic_pairs[{i}].instruction: Must not be empty.")
            if not pair.output.strip():
                errors.append(f"synthetic_pairs[{i}].output: Must not be empty.")

    # Confidence
    if not (0.0 <= aku.confidence <= 1.0):
        errors.append(f"confidence: Must be in [0, 1], got {aku.confidence}")

    return len(errors) == 0, errors


def deduplicate_akus(akus: list[AKU], db: DomainDB) -> list[AKU]:
    """Filter out AKUs that duplicate existing facts in the domain DB.

    An AKU is considered a duplicate if:
    - The exact domain_path already exists in the DB, AND
    - The current_value matches the AKU's fact.

    This prevents "re-training" the same knowledge (model collapse risk).
    """
    unique: list[AKU] = []

    for aku in akus:
        existing = db.get_fact(aku.domain_path)
        if existing and existing.current_value == aku.fact:
            logger.debug(
                "Dedup: Skipping AKU '%s' — already known (value: '%s').",
                aku.domain_path,
                aku.fact[:50],
            )
            continue
        unique.append(aku)

    skipped = len(akus) - len(unique)
    if skipped > 0:
        logger.info("Dedup: %d/%d AKUs skipped as duplicates.", skipped, len(akus))

    return unique


def _normalize_synthetic_pair(pair: dict) -> SyntheticPair:
    """Normalize a synthetic pair dict to a SyntheticPair.

    Local 8B models often use 'input' instead of 'instruction', or
    'question'/'prompt' as aliases. This normalizer handles those
    variants gracefully.
    """
    instruction = (
        pair.get("instruction")
        or pair.get("input")
        or pair.get("question")
        or pair.get("prompt")
        or ""
    )
    output = (
        pair.get("output")
        or pair.get("answer")
        or pair.get("response")
        or ""
    )
    return SyntheticPair(instruction=instruction, output=output)


def parse_akus_from_json(raw: str) -> list[AKU]:
    """Parse a JSON string (or JSON array) into a list of AKU objects.

    Handles both a JSON array of AKUs and a single AKU object.
    Gracefully skips malformed entries with warnings.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse AKU JSON: %s", e)
        return []

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        logger.error("AKU JSON must be an array or object, got %s", type(data).__name__)
        return []

    akus: list[AKU] = []
    for i, item in enumerate(data):
        try:
            # Normalize synthetic_pairs if they're raw dicts
            if "synthetic_pairs" in item:
                item["synthetic_pairs"] = [
                    _normalize_synthetic_pair(p) if isinstance(p, dict) else p
                    for p in item["synthetic_pairs"]
                ]
            aku = AKU(**item)
            valid, errors = validate_aku(aku)
            if valid:
                akus.append(aku)
            else:
                logger.warning("AKU[%d] validation failed: %s", i, "; ".join(errors))
        except Exception as e:
            logger.warning("AKU[%d] parse error: %s", i, e)

    logger.info("Parsed %d valid AKUs from JSON.", len(akus))
    return akus
