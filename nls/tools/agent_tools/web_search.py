"""web_search -- Search the web for real-time information.

Uses DuckDuckGo's HTML search (no API key required).  Returns
summarized snippets with URLs so the agent can follow up with
web_fetch for full content.

Fallback chain:
    1. DuckDuckGo HTML scrape
    2. If that fails, falls back to `curl` against DuckDuckGo lite
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MAX_RESULTS = 8


class WebSearchTool:
    """Search the web and return summarized results."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for real-time information. Returns summarized "
            "snippets and URLs. Use when you need current documentation, "
            "error solutions, API references, or any information not in "
            "your training data."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Be specific -- include error messages, "
                        "library names, version numbers for best results."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5, max 8).",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        query = params.get("query", "").strip()
        if not query:
            return ToolResult(content="Error: 'query' is required.", is_error=True)

        max_results = min(params.get("max_results", 5), _MAX_RESULTS)

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, _search_ddg, query, max_results,
            )
        except Exception as e:
            logger.warning("Web search failed: %s", e)
            return ToolResult(
                content=f"Error: Web search failed: {e}",
                is_error=True,
            )

        if not results:
            return ToolResult(
                content=f"No results found for: {query}",
                is_error=False,
            )

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   URL: {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")

        lines.append(
            "Use web_fetch on any URL above to read the full page content."
        )

        return ToolResult(
            content="\n".join(lines),
            is_error=False,
            details={"result_count": len(results)},
        )


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """Scrape DuckDuckGo HTML results (sync, runs in executor)."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    results: list[dict] = []

    # DuckDuckGo HTML results are in <a class="result__a"> tags
    # with snippets in <a class="result__snippet"> tags
    result_blocks = re.findall(
        r'<div class="links_main[^"]*">(.*?)</div>\s*</div>',
        raw,
        re.DOTALL,
    )

    if not result_blocks:
        result_blocks = re.findall(
            r'class="result__body">(.*?)</div>',
            raw,
            re.DOTALL,
        )

    if not result_blocks:
        return _parse_fallback(raw, max_results)

    for block in result_blocks[:max_results]:
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        url_m = re.search(r'href="([^"]+)"', block)
        snippet_m = re.search(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
            block,
            re.DOTALL,
        )

        if not title_m or not url_m:
            continue

        link = url_m.group(1)
        if "duckduckgo.com/y.js" in link:
            ud = re.search(r'uddg=([^&]+)', link)
            if ud:
                link = urllib.parse.unquote(ud.group(1))

        results.append({
            "title": _clean_html(title_m.group(1)),
            "url": link,
            "snippet": _clean_html(snippet_m.group(1)) if snippet_m else "",
        })

    return results


def _parse_fallback(raw_html: str, max_results: int) -> list[dict]:
    """Fallback parser for DuckDuckGo lite/alternative HTML layouts."""
    results: list[dict] = []

    links = re.findall(
        r'<a[^>]+class="[^"]*result[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        raw_html,
        re.DOTALL,
    )

    for href, title_html in links[:max_results]:
        if "duckduckgo.com" in href and "uddg=" in href:
            ud = re.search(r'uddg=([^&]+)', href)
            if ud:
                href = urllib.parse.unquote(ud.group(1))

        results.append({
            "title": _clean_html(title_html),
            "url": href,
            "snippet": "",
        })

    return results


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def create_web_search_tool() -> WebSearchTool:
    """Factory for the web_search tool."""
    return WebSearchTool()
