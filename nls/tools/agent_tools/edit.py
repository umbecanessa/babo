"""Edit tool -- Surgical find-and-replace with diff output.

The model provides ``old_text`` (the exact text to find) and ``new_text``
(the replacement).  The tool:

    1. Finds the old text in the file (exact match first, then fuzzy)
    2. Ensures the match is unique (rejects ambiguous edits)
    3. Performs the replacement
    4. Returns a unified diff of the changes

Ported from pi-mono's edit tool with adaptations for NLS:
    - BOM-aware (strips BOM before matching, restores after)
    - Line-ending normalization (LF internally, restore original on write)
    - Fuzzy matching (strips leading/trailing whitespace per line)
    - Unified diff generation for context

This is the precision tool.  For wholesale file creation, use ``write``.
For appending or complex multi-section edits, use ``bash`` with ``sed``
or a short Python script.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from pathlib import Path
from typing import Any

from .base import ToolResult
from .write import _NLS_INTERNAL_PATTERN, _resolve_path


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def _strip_bom(text: str) -> tuple[str, str]:
    """Strip UTF-8 BOM if present. Returns (bom, clean_text)."""
    if text.startswith("\ufeff"):
        return "\ufeff", text[1:]
    return "", text


def _detect_line_ending(text: str) -> str:
    """Detect the dominant line ending style."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _restore_line_endings(text: str, original_ending: str) -> str:
    if original_ending == "\r\n":
        return text.replace("\n", "\r\n")
    return text


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def _normalize_for_fuzzy(text: str) -> str:
    """Normalize whitespace for fuzzy matching.

    Strips trailing whitespace from each line and collapses runs of
    blank lines.  This handles the common case where the model's
    ``old_text`` has slightly different indentation or trailing spaces.
    """
    lines = text.split("\n")
    stripped = [line.rstrip() for line in lines]
    return "\n".join(stripped)


def _fuzzy_find(content: str, search: str) -> tuple[bool, int, int, str]:
    """Try exact match first, then fuzzy, then high-similarity window match.

    Returns (found, start_index, match_length, content_for_replacement).
    When fuzzy matching is used, ``content_for_replacement`` is the
    fuzzy-normalized version of the content.
    """
    # Exact match
    idx = content.find(search)
    if idx != -1:
        return True, idx, len(search), content

    # Fuzzy: normalize whitespace
    fuzzy_content = _normalize_for_fuzzy(content)
    fuzzy_search = _normalize_for_fuzzy(search)

    idx = fuzzy_content.find(fuzzy_search)
    if idx != -1:
        return True, idx, len(fuzzy_search), fuzzy_content

    # High-similarity window match: slide a window of search_lines length
    # over the content and accept if similarity >= 90%.  This handles the
    # common case where the LLM hallucinates minor differences (extra
    # spaces, slight typos) in old_text.
    content_lines = fuzzy_content.split("\n")
    search_lines = fuzzy_search.split("\n")
    search_len = len(search_lines)
    _AUTO_ACCEPT_RATIO = 0.90

    if search_len >= 2 and len(content_lines) >= search_len:
        best_ratio = 0.0
        best_start = -1
        for i in range(len(content_lines) - search_len + 1):
            window = content_lines[i : i + search_len]
            sm = difflib.SequenceMatcher(
                None, "\n".join(window), "\n".join(search_lines),
            )
            ratio = sm.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i
        if best_ratio >= _AUTO_ACCEPT_RATIO and best_start >= 0:
            matched_text = "\n".join(
                content_lines[best_start : best_start + search_len],
            )
            char_idx = fuzzy_content.find(matched_text)
            if char_idx != -1:
                return True, char_idx, len(matched_text), fuzzy_content

    return False, -1, 0, content


def _count_occurrences(content: str, search: str) -> int:
    """Count occurrences using fuzzy matching for consistency."""
    fuzzy = _normalize_for_fuzzy(content)
    target = _normalize_for_fuzzy(search)
    if not target:
        return 0
    count = 0
    start = 0
    while True:
        idx = fuzzy.find(target, start)
        if idx == -1:
            break
        count += 1
        start = idx + 1
    return count


def _best_match_snippet(
    content: str, search: str, context_lines: int = 5,
) -> tuple[str, float, int]:
    """Find the closest matching region in *content* for *search*.

    Returns (snippet_with_line_numbers, similarity_ratio, start_line).
    Uses SequenceMatcher on line-level chunks to find the best window.
    """
    content_lines = content.split("\n")
    search_lines = search.split("\n")
    search_len = len(search_lines)

    if not search_lines or not content_lines:
        return "", 0.0, 0

    best_ratio = 0.0
    best_start = 0

    # Slide a window over content_lines looking for the best match
    for i in range(max(1, len(content_lines) - search_len + 1)):
        window = content_lines[i : i + search_len]
        sm = difflib.SequenceMatcher(
            None, "\n".join(window), "\n".join(search_lines),
        )
        ratio = sm.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    # Extract a snippet with surrounding context
    snippet_start = max(0, best_start - context_lines)
    snippet_end = min(len(content_lines), best_start + search_len + context_lines)
    snippet_lines = content_lines[snippet_start:snippet_end]

    numbered = "\n".join(
        f"{snippet_start + j + 1:4d} | {line}"
        for j, line in enumerate(snippet_lines)
    )
    return numbered, best_ratio, best_start + 1


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------


def _generate_diff(old_content: str, new_content: str, path: str) -> tuple[str, int | None]:
    """Generate a unified diff string.

    Returns (diff_string, first_changed_line).
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    diff_lines = list(diff)

    first_changed = None
    for line in diff_lines:
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                first_changed = int(match.group(1))
                break

    return "\n".join(diff_lines), first_changed


# ---------------------------------------------------------------------------
# Edit tool
# ---------------------------------------------------------------------------


class EditTool:
    """Surgical text replacement in files.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    shared_cwd : SharedCWD | None
        Shared mutable CWD holder updated by bash tool.
    ledger : FileLedger | None
        Optional file-change ledger for provenance tracking.
    ledger_meta : dict | None
        Author metadata attached to each ledger entry.
    """

    def __init__(self, cwd: str, shared_cwd: object | None = None,
                 file_state_cache: object | None = None,
                 ledger: object | None = None,
                 ledger_meta: dict | None = None) -> None:
        self._cwd = cwd
        self._shared_cwd = shared_cwd
        self._file_state_cache = file_state_cache
        self._ledger = ledger
        self._ledger_meta: dict = ledger_meta or {"role": "agent"}

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing exact text. The old_text must match "
            "exactly (including whitespace) and must be unique in the file. "
            "Use this for precise, surgical edits."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit (relative or absolute)",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find and replace (must match exactly)",
                },
                "new_text": {
                    "type": "string",
                    "description": "New text to replace the old text with",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    @staticmethod
    def _unescape(text: str) -> str:
        """Fix double-escaped newlines/tabs from model tool call output."""
        if "\\n" not in text and "\\t" not in text:
            return text
        actual_newlines = text.count("\n")
        escaped_newlines = text.count("\\n")
        if escaped_newlines > 0 and actual_newlines <= 1:
            text = text.replace("\\n", "\n")
            text = text.replace("\\t", "\t")
            text = text.replace("\\\\", "\\")
        return text

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "")
        old_text = self._unescape(params.get("old_text", ""))
        new_text = self._unescape(params.get("new_text", ""))

        if not path_str:
            return ToolResult(content="Error: 'path' is required.", is_error=True)
        if not old_text:
            return ToolResult(content="Error: 'old_text' is required.", is_error=True)

        path = _resolve_path(path_str, self._effective_cwd)

        if (
            path.suffix == ".py"
            and _NLS_INTERNAL_PATTERN.search(new_text)
            and not _NLS_INTERNAL_PATTERN.search(old_text)
        ):
            return ToolResult(
                content=(
                    "BLOCKED: You are inserting imports from nls.engine "
                    "internals (autonomic, server_runtime, etc.). "
                    "These are YOUR OWN engine code — not callable APIs.\n"
                    "Allowed: from nls.skills import SkillMeta / "
                    "from nls.engine.agent_tools.base import ToolResult.\n"
                    "For CLI tasks, use bash(command='...') directly."
                ),
                is_error=True,
            )

        if not path.exists():
            hint = "After cd, use paths relative to the NEW directory."
            parts = Path(path_str).parts
            if len(parts) > 1:
                stripped = str(Path(*parts[1:]))
                alt = Path(self._effective_cwd) / stripped
                if alt.exists():
                    hint = (
                        f"Did you mean: `{stripped}`?  "
                        f"Your CWD is already the workspace root — "
                        f"drop the '{parts[0]}/' prefix."
                    )
            return ToolResult(
                content=(
                    f"Error: File not found: {path_str}\n"
                    f"CWD (workspace root): {self._effective_cwd}\n"
                    f"Resolved: {path}\n"
                    f"{hint}"
                ),
                is_error=True,
            )

        # Staleness guard: refuse if file changed since last read.
        if self._file_state_cache is not None:
            stale_err = self._file_state_cache.check(str(path.resolve()))
            if stale_err:
                return ToolResult(content=stale_err, is_error=True)

        # Read file
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        # Normalize
        bom, content = _strip_bom(raw)
        original_ending = _detect_line_ending(content)
        normalized = _normalize_to_lf(content)
        norm_old = _normalize_to_lf(old_text)
        norm_new = _normalize_to_lf(new_text)

        # Try to find the text (exact → whitespace-fuzzy → high-similarity)
        found, idx, match_len, base_content = _fuzzy_find(normalized, norm_old)

        if not found:
            snippet, ratio, near_line = _best_match_snippet(
                normalized, norm_old,
            )
            hint = ""
            if ratio > 0.4 and snippet:
                hint = (
                    f"\n\nClosest match ({ratio:.0%} similar) near line "
                    f"{near_line}:\n```\n{snippet}\n```\n"
                    "READ the file first, then use the EXACT text from "
                    "the file as old_text."
                )
            else:
                lines = normalized.split("\n")
                total = len(lines)
                preview_lines = lines[:40] if total <= 60 else lines[:30]
                numbered = "\n".join(
                    f"{i + 1:4d} | {l}" for i, l in enumerate(preview_lines)
                )
                hint = (
                    f"\n\nThe file has {total} lines. Here are the first "
                    f"{len(preview_lines)}:\n```\n{numbered}\n```\n"
                    "READ the file to see the exact content, then retry."
                )

            return ToolResult(
                content=(
                    f"Error: Could not find the specified old_text in "
                    f"{path_str}.{hint}"
                ),
                is_error=True,
            )

        # Uniqueness check (only for exact + whitespace-fuzzy tiers;
        # the high-similarity tier inherently picks the single best window)
        occurrences = _count_occurrences(normalized, norm_old)
        if occurrences > 1:
            return ToolResult(
                content=(
                    f"Error: Found {occurrences} occurrences in {path_str}. "
                    "The old_text must be unique. Provide more surrounding "
                    "context to make it unambiguous."
                ),
                is_error=True,
            )

        new_content = (
            base_content[:idx] + norm_new + base_content[idx + match_len:]
        )

        if base_content == new_content:
            return ToolResult(
                content=(
                    f"Error: No changes made to {path_str}. "
                    "The replacement produced identical content."
                ),
                is_error=True,
            )

        # Write back
        final = bom + _restore_line_endings(new_content, original_ending)
        try:
            path.write_text(final, encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        if self._file_state_cache is not None:
            self._file_state_cache.update(str(path.resolve()))

        # Record to ledger — use `normalized` (the actual pre-edit file
        # content in LF-normalized form), not `base_content` which may be
        # whitespace-stripped by fuzzy matching and produce a misleading diff.
        if self._ledger is not None:
            try:
                self._ledger.record(
                    path_str, normalized, new_content, "edit", self._ledger_meta,
                )
            except Exception:
                pass

        # Generate diff
        diff_str, first_line = _generate_diff(
            base_content, new_content, path_str,
        )

        return ToolResult(
            content=f"Successfully edited {path_str}.",
            details={
                "diff": diff_str,
                "first_changed_line": first_line,
            },
        )


def create_edit_tool(cwd: str, shared_cwd: object | None = None,
                     file_state_cache: object | None = None,
                     ledger: object | None = None,
                     ledger_meta: dict | None = None) -> EditTool:
    """Factory: create an edit tool configured for a working directory."""
    return EditTool(cwd, shared_cwd=shared_cwd, file_state_cache=file_state_cache,
                    ledger=ledger, ledger_meta=ledger_meta)
