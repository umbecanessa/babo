"""Public-channel safety — refuse out-of-scope or high-risk requests before tools run."""

from __future__ import annotations

import re

from nls.runtime.job_trust import JobDocument, TrustDocument, resolve_channel_overlay

_RISKY_RE = re.compile(
    r"(?i)\b("
    r"delete\s+all|wipe\s+all|ban\s+everyone|remove\s+all\s+channels|"
    r"drop\s+database|rm\s+-rf\s+/|shutdown\s+server|grant\s+admin\s+to\s+everyone|"
    r"ignore\s+(previous|all)\s+instructions|reveal\s+(api\s+)?key|exfiltrat"
    r")\b",
)


def evaluate_public_channel_request(
    user_input: str,
    *,
    job: JobDocument,
    trust: TrustDocument,
    dispatch_source: str,
) -> str | None:
    """Return refusal text if the turn should not proceed on a public channel overlay."""
    ov = resolve_channel_overlay(trust, dispatch_source)
    if ov is None or not ov.public_channel:
        return None
    text = (user_input or "").strip()
    if not text:
        return None
    if _RISKY_RE.search(text):
        return (job.refusal_template or "").strip() or (
            "I cannot help with that in this channel."
        )
    lower = text.lower()
    for item in job.out_of_scope:
        phrase = (item or "").strip().lower()
        if len(phrase) >= 8 and phrase in lower:
            return (job.refusal_template or "").strip() or (
                "That request is outside my role for this channel."
            )
    return None
