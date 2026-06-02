"""web_fetch -- Fetch a URL and return its content as clean text.

Converts HTML pages to readable text by stripping tags, scripts,
styles, and nav elements.  Returns raw text for non-HTML content
(JSON APIs, plain text, etc.).

Uses only stdlib (urllib + html.parser) -- no external dependencies.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MAX_CONTENT_CHARS = 12_000
_TIMEOUT = 15


class WebFetchTool:
    """Fetch a URL and return readable text content."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and return its content as readable text. "
            "Works with web pages (HTML is converted to clean text), "
            "JSON APIs, and plain text. Use after web_search to read "
            "full documentation, Stack Overflow answers, or API docs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch. Must be a full URL starting with http:// or https://.",
                },
                "extract_selector": {
                    "type": "string",
                    "description": (
                        "Optional: CSS-like hint for what to extract. "
                        "Values: 'article' (main content only), 'code' "
                        "(code blocks only), 'full' (everything). Default: 'article'."
                    ),
                },
            },
            "required": ["url"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        url = params.get("url", "").strip()
        if not url:
            return ToolResult(content="Error: 'url' is required.", is_error=True)

        if not url.startswith(("http://", "https://")):
            return ToolResult(
                content="Error: URL must start with http:// or https://",
                is_error=True,
            )

        extract = params.get("extract_selector", "article")

        try:
            content, content_type = await asyncio.get_event_loop().run_in_executor(
                None, _fetch_url, url,
            )
        except Exception as e:
            logger.warning("Web fetch failed for %s: %s", url, e)
            return ToolResult(
                content=f"Error: Failed to fetch {url}: {e}",
                is_error=True,
            )

        is_html = "html" in content_type.lower()

        if is_html:
            text = _html_to_text(content, extract)
        else:
            text = content

        if len(text) > _MAX_CONTENT_CHARS:
            text = text[:_MAX_CONTENT_CHARS] + f"\n\n[Truncated at {_MAX_CONTENT_CHARS} chars]"

        if not text.strip():
            return ToolResult(
                content=f"Fetched {url} but no readable content was extracted.",
                is_error=False,
            )

        header = f"Content from: {url}\n{'=' * 60}\n\n"
        return ToolResult(
            content=header + text,
            is_error=False,
            details={"url": url, "content_type": content_type, "chars": len(text)},
        )


def _fetch_url(url: str) -> tuple[str, str]:
    """Fetch URL content (sync, runs in executor)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        content_type = resp.headers.get("Content-Type", "text/html")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        raw = resp.read()
        return raw.decode(charset, errors="replace"), content_type


def _html_to_text(raw_html: str, extract: str = "article") -> str:
    """Convert HTML to readable text."""
    # Remove script, style, nav, header, footer tags entirely
    cleaned = re.sub(
        r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
        "",
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if extract == "code":
        code_blocks = re.findall(
            r"<(?:pre|code)[^>]*>(.*?)</(?:pre|code)>",
            cleaned,
            re.DOTALL | re.IGNORECASE,
        )
        if code_blocks:
            parts = []
            for i, block in enumerate(code_blocks, 1):
                text = _strip_tags(block)
                parts.append(f"--- Code block {i} ---\n{text}\n")
            return "\n".join(parts)

    if extract == "article":
        # Try to find main content area
        for tag in ["article", "main", 'role="main"', 'id="content"', 'class="content"']:
            if tag.startswith("class=") or tag.startswith("role=") or tag.startswith("id="):
                pattern = rf"<\w+[^>]*{re.escape(tag)}[^>]*>(.*?)</\w+>"
            else:
                pattern = rf"<{tag}[^>]*>(.*?)</{tag}>"
            match = re.search(pattern, cleaned, re.DOTALL | re.IGNORECASE)
            if match and len(match.group(1)) > 200:
                cleaned = match.group(1)
                break

    text = _strip_tags(cleaned)

    # Collapse whitespace while preserving paragraph breaks
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    return text.strip()


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|h[1-6]|li|tr|blockquote)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text


def create_web_fetch_tool() -> WebFetchTool:
    """Factory for the web_fetch tool."""
    return WebFetchTool()
