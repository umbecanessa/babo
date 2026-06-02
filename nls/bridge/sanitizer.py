"""NLS PII Sanitizer — Strip personally identifiable information for Masked-Bridge mode.

Uses regex patterns to detect and redact common PII types:
- Email addresses
- Phone numbers
- API keys / tokens
- File system paths
- IP addresses
- Names (basic heuristic — for production, integrate spaCy NER)

The sanitizer operates on raw conversation text before it's sent to the
Cloud Bridge, ensuring that the Cloud Teacher never sees identifying data.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PII Patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Email addresses
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "[EMAIL_REDACTED]",
    ),
    # Phone numbers (various formats)
    (
        "phone",
        re.compile(
            r"(?:\+?\d{1,3}[-.\s]?)?"
            r"(?:\(?\d{2,4}\)?[-.\s]?)"
            r"\d{3,4}[-.\s]?\d{3,4}"
        ),
        "[PHONE_REDACTED]",
    ),
    # API keys / tokens (long hex or alphanumeric strings)
    (
        "api_key",
        re.compile(
            r"(?:sk-|pk-|api[_-]?key|token|secret|bearer)\s*[=:]\s*"
            r"['\"]?[A-Za-z0-9_\-]{20,}['\"]?"
        ),
        "[API_KEY_REDACTED]",
    ),
    # Generic long tokens (standalone hex strings 32+ chars)
    (
        "hex_token",
        re.compile(r"\b[0-9a-fA-F]{32,}\b"),
        "[TOKEN_REDACTED]",
    ),
    # Windows file paths
    (
        "win_path",
        re.compile(r"[A-Za-z]:\\(?:[^\s\\:*?\"<>|]+\\)*[^\s\\:*?\"<>|]*"),
        "[PATH_REDACTED]",
    ),
    # Unix file paths (absolute)
    (
        "unix_path",
        re.compile(r"(?:/[^\s/]+){3,}"),
        "[PATH_REDACTED]",
    ),
    # IP addresses (IPv4)
    (
        "ipv4",
        re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        "[IP_REDACTED]",
    ),
    # Credit card numbers (basic)
    (
        "credit_card",
        re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "[CC_REDACTED]",
    ),
    # SSN (US format)
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[SSN_REDACTED]",
    ),
]


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------


class PIISanitizer:
    """Redacts PII from text using regex pattern matching.

    For production use, consider augmenting with spaCy NER for name detection.
    The regex approach handles structured PII (emails, keys, paths) well
    but is limited for unstructured PII (names in natural text).
    """

    def __init__(self, extra_patterns: list[tuple[str, re.Pattern, str]] | None = None):
        self.patterns = list(_PATTERNS)
        if extra_patterns:
            self.patterns.extend(extra_patterns)

        # Custom name list (user can add known names to redact)
        self._custom_names: set[str] = set()

    def add_names(self, names: list[str]) -> None:
        """Add specific names to the redaction list."""
        for name in names:
            if len(name) >= 2:  # Avoid redacting single characters
                self._custom_names.add(name.strip())

    def sanitize(self, text: str) -> tuple[str, dict[str, int]]:
        """Sanitize text by redacting all detected PII.

        Returns:
            A tuple of (sanitized_text, redaction_counts) where
            redaction_counts maps PII type names to the number of
            redactions applied.
        """
        counts: dict[str, int] = {}
        result = text

        # Apply regex patterns
        for name, pattern, replacement in self.patterns:
            matches = pattern.findall(result)
            if matches:
                counts[name] = len(matches)
                result = pattern.sub(replacement, result)

        # Apply custom name redaction
        for name in self._custom_names:
            # Case-insensitive word-boundary replacement
            name_pattern = re.compile(re.escape(name), re.IGNORECASE)
            matches = name_pattern.findall(result)
            if matches:
                counts.setdefault("custom_name", 0)
                counts["custom_name"] += len(matches)
                result = name_pattern.sub("[NAME_REDACTED]", result)

        total = sum(counts.values())
        if total > 0:
            logger.info(
                "PII sanitizer: %d redactions applied (%s).",
                total,
                ", ".join(f"{k}={v}" for k, v in counts.items()),
            )

        return result, counts
