"""Live Learning Accumulator — event-driven consolidation for Cryptex rings.

Sits between signal sources (BrainEventBus, delegate lifecycle, team
lifecycle) and the Cryptex rings.  Collects, classifies, and buffers
learning signals, then flushes structured summaries to the appropriate
consolidation domains.

Buffer overflow is handled by LLM-based compounding compression: when a
buffer exceeds its soft cap, a micro-inference compresses it into a dense
summary (~800 chars) that becomes the new base.  New signals append on
top.  Subsequent overflows re-compress — compounding knowledge rather
than discarding it (FIFO).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SOFT_CAP = 2000
_COMPRESSED_TARGET = 800
_SIGNAL_TAG_RE = re.compile(
    r"\[(?:EVALUATE|LEARN|PLAN|IDENTITY|REFLECT|BOND)[:\|][^\]]*\]\s*",
)

_CREDENTIAL_SCRUB_RE = re.compile(
    r"ghp_[A-Za-z0-9]{4,}|gho_[A-Za-z0-9]{4,}|github_pat_[A-Za-z0-9_]{4,}"
    r"|sk-[A-Za-z0-9\-_]{8,}"
    r"|xox[bpsa]-[A-Za-z0-9\-]{8,}"
    r"|(?<=://)([^:]+):([^@]+)@"
    r"|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    """Replace credential tokens with *** to prevent leakage into buffers."""
    return _CREDENTIAL_SCRUB_RE.sub("***", text) if text else text

_COMPRESS_PROMPT = (
    "Compress the following session log into a dense summary preserving:\n"
    "- What was accomplished (outcomes, not process)\n"
    "- What went wrong and how it was recovered\n"
    "- Patterns worth remembering for future sessions\n"
    "- Key files, tools, and architectural decisions\n"
    "Keep under {target} chars. If an existing compressed summary is "
    "provided, merge it with the new entries into a single cohesive summary."
)

_COMPOUND_PROMPT = (
    "You are compounding an AI agent's session memory. The text below is the "
    "agent's rolling consolidation ring — progress, knowledge, and user context "
    "accumulated across multiple loops.\n\n"
    "Rewrite it into THREE labeled sections (total under {target} chars):\n\n"
    "[SessionProgress] Dense narrative of what was accomplished, decisions made, "
    "errors recovered from, and current state. Chronological, factual.\n\n"
    "[ActiveKnowledge] Key technical facts: file paths created, tools used, "
    "architectural decisions, API names, orchestration patterns. No tool-call "
    "boilerplate — only distilled facts.\n\n"
    "[TaskContext] What the user asked for and key constraints.\n\n"
    "Rules:\n"
    "- Every sentence must carry information — drop filler and redundancy\n"
    "- Keep file paths, API names, and architectural choices verbatim\n"
    "- Omit credentials and secrets\n"
    "- All three sections are REQUIRED even if short"
)


# -----------------------------------------------------------------------
# Signal types the accumulator understands
# -----------------------------------------------------------------------

class SignalType:
    TOOL_RESULT = "TOOL_RESULT"
    TURN_END = "TURN_END"
    LOOP_END = "LOOP_END"
    DELEGATE_COMPLETE = "DELEGATE_COMPLETE"
    DELEGATE_ESCALATION = "DELEGATE_ESCALATION"
    WAVE_COMPLETE = "WAVE_COMPLETE"
    PLAN_STEP_CHANGE = "PLAN_STEP_CHANGE"
    MODE_TRANSITION = "MODE_TRANSITION"


# -----------------------------------------------------------------------
# Ring buffer — one per consolidation domain
# -----------------------------------------------------------------------

@dataclass
class _RingBuffer:
    """A single accumulation buffer for one consolidation domain."""

    name: str
    entries: list[str] = field(default_factory=list)
    compressed_base: str = ""
    total_chars: int = 0

    def append(self, text: str) -> None:
        self.entries.append(text)
        self.total_chars += len(text)

    @property
    def overflow(self) -> bool:
        return self._char_count > _SOFT_CAP

    @property
    def _char_count(self) -> int:
        return len(self.compressed_base) + sum(len(e) for e in self.entries)

    @property
    def empty(self) -> bool:
        return not self.entries and not self.compressed_base

    def render(self) -> str:
        parts: list[str] = []
        if self.compressed_base:
            parts.append(self.compressed_base)
        parts.extend(self.entries)
        return "\n".join(parts)

    def clear(self) -> None:
        self.entries.clear()
        self.compressed_base = ""
        self.total_chars = 0


# -----------------------------------------------------------------------
# LearningAccumulator
# -----------------------------------------------------------------------

class LearningAccumulator:
    """Event-driven learning signal collector with compounding compression.

    Subscribes to BrainEventBus signals and delegate/team lifecycle
    callbacks.  Buffers signals into ring-specific buckets, compresses
    on overflow via micro-inference, and flushes to Cryptex consolidation
    rings on periodic + event-driven triggers.
    """

    def __init__(
        self,
        *,
        vllm_client: Any | None = None,
        flush_interval_iters: int = 8,
        wall_clock_flush_seconds: float = 300.0,
    ) -> None:
        self._vllm_client = vllm_client
        self.flush_interval_iters = flush_interval_iters
        self.wall_clock_flush_seconds = wall_clock_flush_seconds

        self._buffers: dict[str, _RingBuffer] = {
            "progress": _RingBuffer(name="progress"),
            "knowledge": _RingBuffer(name="knowledge"),
            "context": _RingBuffer(name="context"),
            "orchestration": _RingBuffer(name="orchestration"),
            "behavioral": _RingBuffer(name="behavioral"),
        }
        self._last_flush_time: float = time.time()
        self._total_flushes: int = 0
        self._compress_in_flight: bool = False

    # ------------------------------------------------------------------
    # Public API: ingest
    # ------------------------------------------------------------------

    def ingest(self, signal_type: str, data: dict[str, Any]) -> None:
        """Classify and buffer a learning signal."""
        try:
            if signal_type == SignalType.TOOL_RESULT:
                self._ingest_tool_result(data)
            elif signal_type == SignalType.TURN_END:
                self._ingest_turn_end(data)
            elif signal_type == SignalType.LOOP_END:
                self._ingest_loop_end(data)
            elif signal_type == SignalType.DELEGATE_COMPLETE:
                self._ingest_delegate_complete(data)
            elif signal_type == SignalType.DELEGATE_ESCALATION:
                self._ingest_delegate_escalation(data)
            elif signal_type == SignalType.WAVE_COMPLETE:
                self._ingest_wave_complete(data)
            elif signal_type == SignalType.PLAN_STEP_CHANGE:
                self._ingest_plan_step_change(data)
            elif signal_type == SignalType.MODE_TRANSITION:
                self._ingest_mode_transition(data)
            else:
                logger.debug("LearningAccumulator: unknown signal type %s", signal_type)
        except Exception:
            logger.debug("LearningAccumulator: ingest failed for %s", signal_type, exc_info=True)

    # ------------------------------------------------------------------
    # BrainEventBus listener methods
    # ------------------------------------------------------------------

    def on_tool_result(self, signal: Any) -> None:
        """BrainEventBus listener for TOOL_RESULT signals."""
        meta = getattr(signal, "metadata", {}) or {}
        success = meta.get("success", True)
        tool_args = getattr(signal, "tool_args", {}) or {}
        self.ingest(SignalType.TOOL_RESULT, {
            "tool_name": getattr(signal, "tool_name", ""),
            "success": success,
            "tool_args": tool_args,
            "result_preview": getattr(signal, "tool_result", "")[:120] if success else "",
            "error": getattr(signal, "tool_result", "")[:200] if not success else "",
            "file_path": getattr(signal, "tool_args", {}).get("path", ""),
            "iteration": getattr(signal, "iteration", 0),
        })

    def on_turn_end(self, signal: Any) -> None:
        """BrainEventBus listener for TURN_END signals."""
        self.ingest(SignalType.TURN_END, {
            "iteration": getattr(signal, "iteration", 0),
            "response_summary": getattr(signal, "response_text", "")[:200],
            "tool_calls": getattr(signal, "metadata", {}).get("tool_calls", []),
        })

    def on_loop_end(self, signal: Any) -> None:
        """BrainEventBus listener for LOOP_END signals."""
        self.ingest(SignalType.LOOP_END, {
            "iterations": getattr(signal, "iteration", 0),
            "elapsed_seconds": getattr(signal, "elapsed_seconds", 0.0),
            "metadata": getattr(signal, "metadata", {}),
        })

    # ------------------------------------------------------------------
    # Delegate/Team lifecycle methods
    # ------------------------------------------------------------------

    def on_member_complete(
        self,
        member_idx: int,
        delegate_number: int,
        task: str,
        status: str,
        result_summary: str,
        iterations: int,
        tool_calls: int,
        elapsed_seconds: float,
        files_written: list[str] | None = None,
        tool_successes: dict[str, int] | None = None,
        tool_errors: dict[str, int] | None = None,
        escalation_count: int = 0,
    ) -> None:
        """Called when a team member (delegate) completes."""
        self.ingest(SignalType.DELEGATE_COMPLETE, {
            "member_idx": member_idx,
            "delegate_number": delegate_number,
            "task": task,
            "status": status,
            "result_summary": result_summary,
            "iterations": iterations,
            "tool_calls": tool_calls,
            "elapsed_seconds": elapsed_seconds,
            "files_written": files_written or [],
            "tool_successes": tool_successes or {},
            "tool_errors": tool_errors or {},
            "escalation_count": escalation_count,
        })

    def on_member_escalation(
        self,
        member_idx: int,
        delegate_number: int,
        task: str,
        reason: str,
        decision: str,
    ) -> None:
        """Called when a delegate escalates."""
        self.ingest(SignalType.DELEGATE_ESCALATION, {
            "member_idx": member_idx,
            "delegate_number": delegate_number,
            "task": task,
            "reason": reason,
            "decision": decision,
        })

    def on_wave_complete(
        self,
        wave_num: int,
        team_name: str,
        member_count: int,
        success_count: int,
        fail_count: int,
        outcome: str,
    ) -> None:
        """Called when an entire delegation wave completes."""
        self.ingest(SignalType.WAVE_COMPLETE, {
            "wave_num": wave_num,
            "team_name": team_name,
            "member_count": member_count,
            "success_count": success_count,
            "fail_count": fail_count,
            "outcome": outcome,
        })

    # ------------------------------------------------------------------
    # Flush — write buffers to Cryptex rings
    # ------------------------------------------------------------------

    def flush(self, target: Any, reason: str = "unknown") -> None:
        """Build structured text from each buffer and push to Cryptex."""
        parts_by_domain: dict[str, str] = {}

        for buf_name, buf in self._buffers.items():
            if buf.empty:
                continue
            parts_by_domain[buf_name] = buf.render()

        if not parts_by_domain:
            return

        consolidated = self._format_consolidation(parts_by_domain)
        if consolidated and target is not None:
            try:
                target.consolidate_session(consolidated)
            except Exception:
                logger.debug(
                    "LearningAccumulator: consolidate_session failed",
                    exc_info=True,
                )

        sizes = {k: len(v) for k, v in parts_by_domain.items()}
        logger.info(
            "LearningAccumulator: flush reason=%s sizes=%s total_flushes=%d",
            reason, sizes, self._total_flushes + 1,
        )

        for buf in self._buffers.values():
            buf.clear()
        self._last_flush_time = time.time()
        self._total_flushes += 1

    def time_since_last_flush(self) -> float:
        return time.time() - self._last_flush_time

    def should_periodic_flush(self, iteration: int) -> bool:
        return (
            iteration > 0
            and iteration % self.flush_interval_iters == 0
            and not self._all_empty()
        )

    def should_wall_clock_flush(self) -> bool:
        return (
            self.time_since_last_flush() > self.wall_clock_flush_seconds
            and not self._all_empty()
        )

    def _all_empty(self) -> bool:
        return all(buf.empty for buf in self._buffers.values())

    async def compress_and_flush(self, target: Any, reason: str = "unknown") -> None:
        """Compress all substantial buffers, then flush to Cryptex.

        Unlike the sync ``flush()``, this runs LLM compression on any
        buffer with content, producing coherent narratives instead of
        raw entry lists.  Use for loop-end and other important flushes.

        For loop-end flushes, compression fires on ANY non-empty buffer
        (threshold=0) to ensure even short loops produce quality content.
        """
        _is_loop_end = reason.startswith("loop-end")
        _threshold = 0 if _is_loop_end else 300

        if self._vllm_client is None and _is_loop_end:
            logger.warning(
                "LearningAccumulator: no vLLM client — "
                "loop-end flush will use raw buffers (no compression)"
            )

        if self._vllm_client is not None and not self._compress_in_flight:
            self._compress_in_flight = True
            try:
                for buf in self._buffers.values():
                    raw = buf.render()
                    if raw and len(raw) > _threshold:
                        await self._compress_buffer_force(buf, raw)
            finally:
                self._compress_in_flight = False

        self.flush(target, reason=reason)

        if self._vllm_client is not None:
            await self._compound_ring(target)

    async def _compress_buffer_force(
        self, buf: "_RingBuffer", raw: str,
    ) -> None:
        """Compress a buffer regardless of soft cap."""
        if self._vllm_client is None:
            return
        try:
            compressed = await self._llm_compress(raw, buf.name)
            if compressed and len(compressed) < len(raw):
                buf.compressed_base = compressed
                buf.entries.clear()
                buf.total_chars = len(compressed)
                logger.info(
                    "LearningAccumulator: compressed %s %d->%d chars",
                    buf.name, len(raw), len(compressed),
                )
        except Exception:
            logger.warning(
                "LearningAccumulator: compress failed for %s (vLLM unreachable?)",
                buf.name, exc_info=True,
            )

    async def _compound_ring(self, target: Any) -> None:
        """Re-compress existing consolidation ring content via LLM.

        After each loop-end flush appends new content to the ring, the
        combined text can become a mess of ``" | "``-joined fragments.
        This pass reads the ring back, runs LLM compounding, and writes
        a clean dense narrative that replaces the raw concatenation.

        Only fires when combined ring content > _COMPOUND_THRESHOLD.
        """
        _COMPOUND_THRESHOLD = 600
        _COMPOUND_TARGET = 1800
        if target is None or self._vllm_client is None:
            return

        try:
            existing = ""
            if hasattr(target, "get_consolidation_context"):
                existing = target.get_consolidation_context()
            if not existing or len(existing) < _COMPOUND_THRESHOLD:
                return

            system_msg = _COMPOUND_PROMPT.format(target=_COMPOUND_TARGET)
            result = await self._vllm_client.generate(
                adapter_name=None,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": existing[:6000]},
                ],
                max_tokens=800,
                temperature=0.3,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            compounded = (
                result.text if hasattr(result, "text") else str(result or "")
            ).strip()
            if "<think>" in compounded:
                compounded = re.sub(
                    r"<think>.*?</think>", "", compounded, flags=re.DOTALL,
                ).strip()

            if not compounded or len(compounded) < 50:
                return
            if len(compounded) >= len(existing):
                return

            if hasattr(target, "replace_consolidation"):
                target.replace_consolidation(compounded)
                logger.info(
                    "LearningAccumulator: compounded ring %d->%d chars",
                    len(existing), len(compounded),
                )
        except Exception:
            logger.warning(
                "LearningAccumulator: ring compounding failed (vLLM unreachable?)",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Compression — LLM-based compounding
    # ------------------------------------------------------------------

    async def compress_overflow_buffers(self) -> None:
        """Check all buffers and compress any that exceed the soft cap.

        Uses micro-inference when a vllm_client is available; falls back
        to simple truncation otherwise.
        """
        if self._compress_in_flight:
            return
        self._compress_in_flight = True
        try:
            for buf in self._buffers.values():
                if buf.overflow:
                    await self._compress_buffer(buf)
        finally:
            self._compress_in_flight = False

    async def _compress_buffer(self, buf: _RingBuffer) -> None:
        """Compress a single buffer via micro-inference or truncation."""
        raw = buf.render()
        if len(raw) <= _SOFT_CAP:
            return

        if self._vllm_client is not None:
            try:
                compressed = await self._llm_compress(raw, buf.name)
                if compressed and len(compressed) < len(raw):
                    buf.compressed_base = compressed
                    buf.entries.clear()
                    buf.total_chars = len(compressed)
                    logger.info(
                        "LearningAccumulator: compressed %s %d->%d chars",
                        buf.name, len(raw), len(compressed),
                    )
                    return
            except Exception:
                logger.debug(
                    "LearningAccumulator: LLM compress failed for %s, falling back",
                    buf.name, exc_info=True,
                )

        self._truncate_fallback(buf, raw)

    async def _llm_compress(self, text: str, buffer_name: str) -> str:
        """Run micro-inference to compress buffer contents."""
        system_msg = _COMPRESS_PROMPT.format(target=_COMPRESSED_TARGET)
        user_msg = f"Buffer: {buffer_name}\n\n{text}"

        result = await self._vllm_client.generate(
            adapter_name=None,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg[:4000]},
            ],
            max_tokens=400,
            temperature=0.3,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        out = (
            result.text if hasattr(result, "text") else str(result or "")
        ).strip()

        if "<think>" in out:
            out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()

        return out[:_COMPRESSED_TARGET + 200]

    def _truncate_fallback(self, buf: _RingBuffer, raw: str) -> None:
        """Simple truncation: keep the last _SOFT_CAP chars."""
        truncated = raw[-_SOFT_CAP:]
        cut = truncated.find("\n")
        if cut != -1 and cut < 200:
            truncated = truncated[cut + 1:]
        buf.compressed_base = truncated
        buf.entries.clear()
        buf.total_chars = len(truncated)
        logger.info(
            "LearningAccumulator: truncated %s %d->%d chars (fallback)",
            buf.name, len(raw), len(truncated),
        )

    # ------------------------------------------------------------------
    # Formatting: buffer content -> consolidation summary
    # ------------------------------------------------------------------

    @staticmethod
    def _format_consolidation(parts: dict[str, str]) -> str:
        """Format buffer contents into the [Progress]/[Knowledge]/[Context]
        structure that CryptexMemory.consolidate_session() expects."""
        out: list[str] = []
        if "progress" in parts:
            out.append(f"[Progress] {parts['progress']}")
        if "knowledge" in parts:
            out.append(f"[Knowledge] {parts['knowledge']}")
        if "context" in parts:
            out.append(f"[Context] {parts['context']}")

        orch = parts.get("orchestration", "")
        behav = parts.get("behavioral", "")
        extra = "; ".join(filter(None, [orch, behav]))
        if extra:
            out.append(f"[Knowledge] Orchestration: {extra}")

        return "\n".join(out)

    # ------------------------------------------------------------------
    # Internal ingestion helpers
    # ------------------------------------------------------------------

    def _ingest_tool_result(self, data: dict) -> None:
        name = data.get("tool_name", "?")
        success = data.get("success", True)
        path = data.get("file_path", "")
        args = data.get("tool_args", {})
        preview = _scrub(data.get("result_preview", ""))

        if success:
            if name in ("write", "edit") and path:
                self._buffers["knowledge"].append(f"{name}: {path}")
            elif name == "bash":
                cmd = _scrub((args.get("command", "") or "")[:120])[:80]
                self._buffers["progress"].append(f"bash({cmd}): OK")
            elif name in ("read", "glob", "grep", "list_dir"):
                target = path or args.get("pattern", "") or args.get("query", "")
                if target:
                    self._buffers["knowledge"].append(f"{name}: {target[:60]}")
            elif name in ("web_search", "web_fetch"):
                query = _scrub(args.get("query", "") or args.get("url", ""))
                if query:
                    self._buffers["knowledge"].append(f"{name}: {query[:80]}")
            elif name in ("plan", "team", "todo"):
                action = args.get("action", "")
                brief = preview[:80] if preview else ""
                self._buffers["orchestration"].append(f"{name}({action}): {brief}")
        else:
            err = _scrub(data.get("error", "")[:200])[:100]
            self._buffers["knowledge"].append(f"{name} FAILED: {err}")

    def _ingest_turn_end(self, data: dict) -> None:
        iteration = data.get("iteration", 0)
        summary = _scrub(data.get("response_summary", ""))
        summary = _SIGNAL_TAG_RE.sub("", summary).strip()
        if not summary:
            return
        summary = summary[:200]
        tc = data.get("tool_calls", [])
        if tc:
            tools = ", ".join(t[:20] for t in tc[:3])
            self._buffers["progress"].append(
                f"iter {iteration} [{tools}]: {summary}"
            )
        else:
            self._buffers["progress"].append(
                f"(response) {summary}"
            )

    def _ingest_loop_end(self, data: dict) -> None:
        iters = data.get("iterations", 0)
        tc = data.get("total_tool_calls", 0)
        delegate_count = data.get("delegate_count", 0)
        elapsed = data.get("elapsed_seconds", 0.0)
        meta = data.get("metadata", {})
        exit_reason = meta.get("exit_reason", "")
        final_preview = _scrub(data.get("final_response_preview", ""))[:120]
        parts = [f"Loop ended ({exit_reason}): {iters} iters, {tc} tools, {elapsed:.0f}s"]
        if delegate_count:
            parts.append(f", {delegate_count} delegate(s)")
        if final_preview:
            parts.append(f" → {final_preview}")
        self._buffers["progress"].append("".join(parts))

    def _ingest_delegate_complete(self, data: dict) -> None:
        num = data.get("delegate_number", "?")
        task = data.get("task", "")[:60]
        status = data.get("status", "?")
        iters = data.get("iterations", 0)
        tc = data.get("tool_calls", 0)
        files = data.get("files_written", [])
        tool_s = data.get("tool_successes", {})
        tool_e = data.get("tool_errors", {})
        esc = data.get("escalation_count", 0)
        summary = data.get("result_summary", "")[:200]

        tools_line = ""
        if tool_s:
            top = sorted(tool_s.items(), key=lambda x: -x[1])[:5]
            tools_line = ", ".join(f"{t} x{n}" for t, n in top)

        member_str = (
            f"[Delegate #{num} \"{task}\"] {status.upper()} "
            f"({iters} iters, {tc} tools)"
        )
        if tools_line:
            member_str += f"\n  Tools: {tools_line}"
        if files:
            member_str += f"\n  Files: {', '.join(files[-8:])}"
        if esc:
            member_str += f"\n  Escalations: {esc}"
        if tool_e:
            err_line = ", ".join(f"{t} x{n}" for t, n in sorted(tool_e.items(), key=lambda x: -x[1])[:3])
            member_str += f"\n  Errors: {err_line}"
        if summary:
            member_str += f"\n  Outcome: {summary}"

        self._buffers["knowledge"].append(member_str)
        self._buffers["orchestration"].append(
            f"Delegate #{num} ({task[:40]}): {status}, {iters} iters"
            + (f", {esc} esc" if esc else "")
        )
        if status == "done":
            self._buffers["behavioral"].append(
                f"Delegate #{num} succeeded in {iters} iters for: {task[:60]}"
            )
        elif status in ("failed", "error"):
            self._buffers["behavioral"].append(
                f"Delegate #{num} FAILED for: {task[:60]}; errors={list(tool_e.keys())[:3]}"
            )

    def _ingest_delegate_escalation(self, data: dict) -> None:
        num = data.get("delegate_number", "?")
        task = data.get("task", "")[:40]
        reason = data.get("reason", "")
        decision = data.get("decision", "")
        self._buffers["orchestration"].append(
            f"Escalation #{num} ({task}): {reason} -> {decision}"
        )
        self._buffers["behavioral"].append(
            f"Escalation pattern: {reason} on {task[:40]} -> resolved by {decision}"
        )

    def _ingest_wave_complete(self, data: dict) -> None:
        wave = data.get("wave_num", "?")
        name = data.get("team_name", "")
        total = data.get("member_count", 0)
        ok = data.get("success_count", 0)
        fail = data.get("fail_count", 0)
        outcome = data.get("outcome", "")
        self._buffers["orchestration"].append(
            f"Wave {wave} ({name}): {outcome.upper()} — {ok}/{total} succeeded"
            + (f", {fail} failed" if fail else "")
        )
        self._buffers["progress"].append(
            f"Wave {wave} completed: {ok}/{total} delegates succeeded"
        )

    def _ingest_plan_step_change(self, data: dict) -> None:
        label = data.get("label", "")
        old = data.get("old_status", "")
        new = data.get("new_status", "")
        if old != new:
            self._buffers["orchestration"].append(
                f"Plan step \"{label[:40]}\": {old} -> {new}"
            )

    def _ingest_mode_transition(self, data: dict) -> None:
        from_mode = data.get("from_mode", "")
        to_mode = data.get("to_mode", "")
        reason = data.get("reason", "")
        self._buffers["context"].append(
            f"Mode: {from_mode} -> {to_mode}" + (f" ({reason})" if reason else "")
        )
