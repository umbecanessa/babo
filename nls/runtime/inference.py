"""NLS Inference — Chat interface via llama-cpp-python.

Wraps the hydrated Llama model in a conversation-aware interface.
Manages the system prompt (minimal identity root) and streams responses.

Includes the **Inference Interceptor** — a dual-layer system that:
  1. Pre-detects factual domains from the user's query (query-side).
  2. Monitors token-level log-probabilities during streaming.
  3. Injects the SQLite Domain Ledger value when confidence drops.

This mirrors human cognition: you try to recall from memory (weights),
and if the signal is weak, you check your notes (SQLite).  Over many
epochs of cumulative training (LTP), the weight-based recall strengthens
and the SQLite fallback becomes unnecessary — the fact "graduates" from
referential to semantic memory.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Generator

from nls.models import ChainState, ConversationTurn, Fact, InterceptionEvent

if TYPE_CHECKING:
    from llama_cpp import Llama

    from nls.ledger.domain_db import DomainDB

logger = logging.getLogger(__name__)

# The minimal "Identity Root" system prompt — replaces the traditional
# 5000-token system prompt. The real identity is in the weights.
_DEFAULT_SYSTEM_PROMPT = (
    "You are a personal AI assistant with persistent memory. "
    "Your knowledge and personality are embedded in your neural weights "
    "through the Neural Ledger State system. Respond naturally and "
    "helpfully. Your identity, preferences, and learned skills are "
    "already part of who you are — you don't need to be reminded. "
    "You may have tools available (file reading, terminal, web search, etc.) "
    "that were taught to you through structured onboarding. Use them "
    "proactively when relevant."
)


def build_system_prompt(state: ChainState) -> str:
    """Build a minimal system prompt for the hydrated agent.

    In the NLS architecture, the system prompt is intentionally small
    (~500 tokens). The agent's real identity lives in the weights, not
    in the prompt. This just provides basic framing.
    """
    from datetime import datetime as _dt

    parts = [_DEFAULT_SYSTEM_PROMPT]

    # Add sovereignty mode context
    if state.sovereignty_mode.value == "local":
        parts.append("You operate in fully local mode — no data leaves this device.")

    # Add chain height context (so the agent knows its "age")
    if state.current_height > 0:
        parts.append(
            f"You have evolved through {state.current_height} learning cycles."
        )

    # Add temporal awareness (the agent needs to know what day it is)
    parts.append(f"Today's date is {_dt.now().strftime('%A, %B %d, %Y')}.")

    return "\n".join(parts)


def chat_completion(
    model: "Llama",
    messages: list[dict[str, str]],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    stream: bool = False,
) -> str | Generator[str, None, None]:
    """Run a chat completion on the hydrated model.

    Args:
        model: The hydrated Llama instance.
        messages: List of message dicts with 'role' and 'content'.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature.
        top_p: Top-p (nucleus) sampling.
        stream: If True, returns a generator yielding tokens.

    Returns:
        The assistant's response text, or a generator if streaming.
    """
    if stream:
        return _stream_completion(model, messages, max_tokens, temperature, top_p)

    response = model.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )

    content = response["choices"][0]["message"]["content"]
    logger.debug("Generated %d chars (non-streaming).", len(content))
    return content


def _stream_completion(
    model: "Llama",
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Generator[str, None, None]:
    """Stream tokens from the model one at a time."""
    stream = model.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=True,
    )

    for chunk in stream:
        delta = chunk["choices"][0].get("delta", {})
        token = delta.get("content", "")
        if token:
            yield token


def turns_to_messages(
    turns: list[ConversationTurn],
    system_prompt: str,
) -> list[dict[str, str]]:
    """Convert ConversationTurn objects into the message format expected by llama-cpp.

    Prepends the system prompt as the first message.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for turn in turns:
        messages.append({"role": turn.role, "content": turn.content})
    return messages


# ---------------------------------------------------------------------------
# Inference Interceptor — Domain Detection
# ---------------------------------------------------------------------------


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "of", "in", "to", "for", "with", "on", "at", "by", "from",
    "as", "it", "its", "this", "that", "and", "or", "but", "not",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she",
    "him", "her", "they", "them", "their", "what", "which", "who",
    "how", "when", "where", "why", "all", "some", "no", "any",
})


def _domain_path_to_keywords(domain_path: str) -> list[str]:
    """Extract searchable keywords from a hierarchical domain path.

    E.g. ``'User.Personal.ServerPassword'`` ->
    ``['user', 'personal', 'server', 'password']``

    CamelCase segments are split into individual words and lowered.
    Stop words and very short tokens (< 3 chars) are excluded to
    prevent false matches on common words like "the", "a", "I".
    """
    segments = domain_path.split(".")
    keywords: list[str] = []
    for seg in segments:
        words = re.findall(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z]|$)", seg)
        for w in words:
            low = w.lower()
            if low not in _STOP_WORDS and len(low) >= 3:
                keywords.append(low)
    return keywords


def _content_to_keywords(text: str) -> list[str]:
    """Extract searchable keywords from fact content or canonical question."""
    words = re.findall(r"[a-zA-Z]+", text)
    return [
        w.lower() for w in words
        if w.lower() not in _STOP_WORDS and len(w) >= 3
    ]


_SYNONYM_GROUPS: list[frozenset[str]] = [
    frozenset({"flight", "fly", "flying", "airline", "plane", "airport",
               "boarding", "departure", "arrival", "klm", "ryanair",
               "easyjet", "lufthansa", "booking"}),
    frozenset({"travel", "trip", "journey", "vacation", "holiday",
               "itinerary", "destination", "hotel", "accommodation"}),
    frozenset({"calendar", "schedule", "event", "meeting", "appointment",
               "agenda", "reminder"}),
    frozenset({"email", "mail", "inbox", "gmail", "message", "unread"}),
    frozenset({"database", "postgres", "postgresql", "sqlite", "mysql",
               "mongodb", "supabase", "storage", "schema"}),
    frozenset({"deploy", "deployment", "hosting", "railway", "vercel",
               "heroku", "render", "server", "cloud"}),
    frozenset({"frontend", "framework", "react", "vue", "angular", "svelte",
               "nextjs", "vite", "tailwind"}),
    frozenset({"backend", "api", "fastapi", "express", "django", "flask",
               "python", "node", "rest"}),
    frozenset({"auth", "authentication", "login", "jwt", "oauth", "session",
               "password", "token", "clerk"}),
    frozenset({"transcription", "audio", "speech", "assemblyai", "whisper",
               "deepgram", "recording"}),
    frozenset({"coaching", "evaluation", "assessment", "analysis", "icf",
               "mentor", "competency"}),
    frozenset({"name", "called", "call", "identity", "who"}),
    frozenset({"github", "git", "repo", "repository", "account",
               "username", "commit", "branch"}),
]


def _expand_query_with_synonyms(query_lower: str) -> str:
    """Append synonym keywords to the query string for broader matching."""
    extra: list[str] = []
    for group in _SYNONYM_GROUPS:
        if any(kw in query_lower for kw in group):
            extra.extend(group)
    if extra:
        return query_lower + " " + " ".join(extra)
    return query_lower


def detect_factual_domains(
    query: str,
    db: "DomainDB",
    min_keyword_hits: int = 1,
    project_id: str = "",
) -> list[Fact]:
    """Pre-detect which factual domains a user query is likely asking about.

    When ``project_id`` is set, uses ``get_facts_in_context`` so only
    global + domain + matching project facts are considered.  Otherwise
    falls back to all facts.
    """
    if project_id:
        all_facts = db.get_facts_in_context(project_id)
    else:
        all_facts = db.get_all_facts()
    if not all_facts:
        return []

    query_lower = _expand_query_with_synonyms(query.lower())
    scored: list[tuple[float, Fact]] = []

    for fact in all_facts:
        domain_kws = _domain_path_to_keywords(fact.domain_path)
        domain_hits = sum(1 for kw in domain_kws if kw in query_lower)

        content_kws = _content_to_keywords(fact.current_value or "")
        if fact.canonical_question:
            content_kws.extend(_content_to_keywords(fact.canonical_question))
        content_kws_unique = list(dict.fromkeys(content_kws))
        content_hits = sum(1 for kw in content_kws_unique if kw in query_lower)

        score = domain_hits * 2 + content_hits
        if score >= min_keyword_hits:
            scored.append((score, fact))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [fact for _, fact in scored]


# ---------------------------------------------------------------------------
# Inference Interceptor — Token Confidence Monitor
# ---------------------------------------------------------------------------

# Default configuration
_DEFAULT_CONFIDENCE_THRESHOLD = -2.0  # logprob; ~13% probability
_DEFAULT_MIN_UNCERTAIN_TOKENS = 3  # consecutive low-confidence tokens to trigger


class InferenceInterceptor:
    """Monitors token confidence during streaming and injects SQLite fallback.

    The interceptor implements the "try to remember, then check your notes"
    pattern.  It watches the log-probability of each generated token and,
    when confidence drops below a threshold for several consecutive tokens
    while the model is responding about a known factual domain, replaces the
    uncertain output with the authoritative value from the Domain Ledger.

    Lifecycle:
      1. Created with pre-loaded candidate facts (from ``detect_factual_domains``).
      2. Fed tokens one at a time via ``process_token()``.
      3. Returns ``(tokens_to_yield, should_stop)`` — the caller yields the
         tokens and optionally stops generation.

    Attributes:
        db: DomainDB instance for SQLite lookups.
        candidates: Facts that may be relevant to this query (best match first).
        threshold: Log-probability below which a token is "uncertain."
        min_uncertain: How many consecutive uncertain tokens trigger injection.
        events: List of InterceptionEvents that occurred during this generation.
    """

    def __init__(
        self,
        db: "DomainDB",
        candidates: list[Fact],
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        min_uncertain_tokens: int = _DEFAULT_MIN_UNCERTAIN_TOKENS,
    ):
        self.db = db
        self.candidates = candidates
        self.threshold = confidence_threshold
        self.min_uncertain = min_uncertain_tokens
        self.events: list[InterceptionEvent] = []

        # Internal state
        self._uncertain_buffer: list[str] = []  # tokens held back during uncertainty
        self._uncertain_logprobs: list[float] = []  # their logprobs
        self._injected = False  # only inject once per generation

    def process_token(
        self,
        token: str,
        logprob: float | None,
    ) -> tuple[list[str], bool]:
        """Process a single streamed token and decide what to yield.

        Args:
            token: The generated token text.
            logprob: The log-probability of this token (None if unavailable).

        Returns:
            A tuple of ``(tokens_to_yield, should_stop)``:
            - ``tokens_to_yield``: list of strings to stream to the user.
              May be empty (buffering), the original token, or a SQLite
              injected value.
            - ``should_stop``: if True, the caller should stop generation
              after yielding these tokens.
        """
        # If we've already injected or have no candidates, pass through
        if self._injected or not self.candidates or logprob is None:
            return [token], False

        # Check if this token is uncertain
        if logprob < self.threshold:
            # Buffer the uncertain token (don't yield yet)
            self._uncertain_buffer.append(token)
            self._uncertain_logprobs.append(logprob)

            # Check if we've hit the threshold for injection
            if len(self._uncertain_buffer) >= self.min_uncertain:
                return self._inject()

            # Still buffering — yield nothing yet
            return [], False

        else:
            # Token is confident — flush any buffered tokens and yield all
            if self._uncertain_buffer:
                flushed = self._uncertain_buffer.copy()
                self._uncertain_buffer.clear()
                self._uncertain_logprobs.clear()
                flushed.append(token)
                return flushed, False

            return [token], False

    def _inject(self) -> tuple[list[str], bool]:
        """Replace buffered uncertain tokens with the SQLite value.

        Uses the best-matching candidate fact.  Logs an InterceptionEvent.
        """
        self._injected = True
        best_fact = self.candidates[0]

        avg_lp = (
            sum(self._uncertain_logprobs) / len(self._uncertain_logprobs)
            if self._uncertain_logprobs
            else 0.0
        )

        event = InterceptionEvent(
            domain_path=best_fact.domain_path,
            injected_value=best_fact.current_value,
            original_tokens=self._uncertain_buffer.copy(),
            avg_logprob=avg_lp,
            timestamp=datetime.utcnow(),
        )
        self.events.append(event)

        logger.info(
            "Interceptor fired: domain=%s, injected=%r, replaced %d tokens (avg_logprob=%.3f)",
            best_fact.domain_path,
            best_fact.current_value,
            len(self._uncertain_buffer),
            avg_lp,
        )

        # Clear buffer
        self._uncertain_buffer.clear()
        self._uncertain_logprobs.clear()

        # Return the injected value and signal to stop generation
        return [best_fact.current_value], True

    def flush(self) -> list[str]:
        """Flush any remaining buffered tokens at end of generation.

        If generation finishes while tokens are still buffered (uncertain
        but didn't reach the injection threshold), release them as-is.
        """
        if self._uncertain_buffer:
            tokens = self._uncertain_buffer.copy()
            self._uncertain_buffer.clear()
            self._uncertain_logprobs.clear()
            return tokens
        return []


# ---------------------------------------------------------------------------
# Inference Interceptor — Intercepted Chat Completion
# ---------------------------------------------------------------------------


def _stream_with_logprobs(
    model: "Llama",
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Generator[tuple[str, float | None], None, None]:
    """Stream tokens WITH log-probabilities from the model.

    Yields ``(token_text, logprob)`` tuples.  ``logprob`` is ``None`` if
    the model/backend does not return it for a given chunk.
    """
    stream = model.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=True,
        logprobs=True,
        top_logprobs=3,
    )

    for chunk in stream:
        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        token = delta.get("content", "")
        if not token:
            continue

        # Extract logprob from the chunk
        logprob: float | None = None
        lp_data = choice.get("logprobs")
        if lp_data:
            # llama-cpp-python format: logprobs.content[].logprob
            content_logprobs = lp_data.get("content")
            if content_logprobs and len(content_logprobs) > 0:
                logprob = content_logprobs[0].get("logprob")
            # Fallback: older format with token_logprobs list
            if logprob is None:
                token_lps = lp_data.get("token_logprobs")
                if token_lps and len(token_lps) > 0:
                    logprob = token_lps[0]

        yield token, logprob


def intercepted_chat_completion(
    model: "Llama",
    messages: list[dict[str, str]],
    db: "DomainDB | None" = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    stream: bool = True,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    min_uncertain_tokens: int = _DEFAULT_MIN_UNCERTAIN_TOKENS,
) -> str | Generator[str, None, None]:
    """Chat completion with Inference Interceptor (LTP fallback).

    If a ``DomainDB`` is provided, the interceptor:
      1. Scans the user's last message for factual domain keywords.
      2. Pre-loads matching facts from SQLite as fallback candidates.
      3. Streams generation with log-probabilities enabled.
      4. When confidence drops below ``confidence_threshold`` for
         ``min_uncertain_tokens`` consecutive tokens in a known domain,
         injects the SQLite value and stops generation of that segment.

    If ``db`` is None, this falls through to the standard
    ``chat_completion()`` with no interception.

    Args:
        model: The hydrated Llama instance.
        messages: List of message dicts with 'role' and 'content'.
        db: Optional DomainDB for the Inference Interceptor.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature.
        top_p: Top-p (nucleus) sampling.
        stream: If True, returns a generator yielding tokens.
        confidence_threshold: Log-probability below which a token is uncertain.
        min_uncertain_tokens: Consecutive uncertain tokens before injection.

    Returns:
        The assistant's response text (or a generator if streaming).
        When interception occurs, the injected value replaces the uncertain
        segment in the output.
    """
    # No DB → fall through to standard completion
    if db is None:
        return chat_completion(
            model, messages, max_tokens, temperature, top_p, stream,
        )

    # Extract the last user message for domain detection
    user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_query = msg.get("content", "")
            break

    # Pre-load candidate facts from SQLite
    candidates = detect_factual_domains(user_query, db)

    if not candidates:
        # No relevant facts in the ledger — standard completion
        logger.debug("Interceptor: no domain candidates found, using standard completion.")
        return chat_completion(
            model, messages, max_tokens, temperature, top_p, stream,
        )

    logger.info(
        "Interceptor: %d candidate fact(s) pre-loaded: %s",
        len(candidates),
        [f.domain_path for f in candidates[:5]],
    )

    # Create the interceptor
    interceptor = InferenceInterceptor(
        db=db,
        candidates=candidates,
        confidence_threshold=confidence_threshold,
        min_uncertain_tokens=min_uncertain_tokens,
    )

    if stream:
        return _intercepted_stream(
            model, messages, max_tokens, temperature, top_p, interceptor,
        )
    else:
        # Non-streaming: collect all tokens, apply interception, return string
        parts: list[str] = []
        for token_text, logprob in _stream_with_logprobs(
            model, messages, max_tokens, temperature, top_p,
        ):
            to_yield, should_stop = interceptor.process_token(token_text, logprob)
            parts.extend(to_yield)
            if should_stop:
                break
        # Flush any remaining buffered tokens
        parts.extend(interceptor.flush())
        result = "".join(parts)
        logger.debug(
            "Intercepted completion: %d chars, %d interception event(s).",
            len(result),
            len(interceptor.events),
        )
        return result


def _intercepted_stream(
    model: "Llama",
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    top_p: float,
    interceptor: InferenceInterceptor,
) -> Generator[str, None, None]:
    """Stream tokens through the Inference Interceptor.

    Each token's log-probability is checked against the interceptor.  When
    confidence drops in a factual domain, the SQLite value is injected and
    streaming continues (or stops for that fact).
    """
    for token_text, logprob in _stream_with_logprobs(
        model, messages, max_tokens, temperature, top_p,
    ):
        to_yield, should_stop = interceptor.process_token(token_text, logprob)
        for t in to_yield:
            yield t
        if should_stop:
            break

    # Flush any remaining buffered tokens
    for t in interceptor.flush():
        yield t
