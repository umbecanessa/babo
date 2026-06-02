"""NLS Browser Engine -- Playwright-backed headless browser for real web browsing.

Gives Babo a real Chromium browser that can:
  - Render JavaScript (critical for modern web)
  - Navigate to any URL and extract readable content
  - Search the web via DuckDuckGo
  - Follow links from a page
  - Deep-browse: search -> read top results -> follow interesting links
  - Interactive actions: click, fill, screenshot via semantic refs
  - AI snapshots: accessibility tree with ref IDs (like OpenClaw)

The browser is lazily initialized on first use and persists across the
entire agent session for efficiency. Manages its own context and handles
cleanup on shutdown.

Interactive mode (Phase 1 - Agentic):
  The engine maintains a persistent page (``_active_page``) for multi-step
  browser sessions. Actions use semantic accessibility refs (``ref=e3``)
  instead of CSS selectors, making the agent's browser control universal
  and robust across any website.

Design principles:
  - Config-driven (all knobs in runtime.json)
  - Lazy initialization (no browser until first search/read)
  - Graceful degradation (falls back to urllib if Playwright unavailable)
  - Thread-safe via a simple lock on all browser operations
  - Production-grade error handling and timeouts
  - AI snapshots with semantic refs for reliable element targeting
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content extraction helpers (fallback when Playwright unavailable)
# ---------------------------------------------------------------------------


class _ReadableTextParser(HTMLParser):
    """Extract readable text from HTML, stripping scripts/styles/nav."""

    _SKIP_TAGS = frozenset({
        "script", "style", "noscript", "iframe", "svg",
        "nav", "footer", "header", "aside", "form",
    })

    def __init__(self):
        super().__init__()
        self._text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"):
            self._text_parts.append("\n")
        elif tag in ("br", "div", "section", "article"):
            self._text_parts.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._text_parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self._text_parts)
        lines = [line.strip() for line in raw.split("\n")]
        cleaned = []
        for line in lines:
            if line:
                cleaned.append(line)
            elif cleaned and cleaned[-1] != "":
                cleaned.append("")
        return "\n".join(cleaned).strip()


# ---------------------------------------------------------------------------
# Browser Configuration
# ---------------------------------------------------------------------------


@dataclass
class BrowserConfig:
    """Configuration for the BrowserEngine."""

    headless: bool = True
    channel: str = ""
    """Playwright browser channel (e.g. 'chrome', 'msedge').
    When set, Playwright uses the user's real installed browser
    instead of downloading a bundled Chromium."""
    timeout_ms: int = 15000
    navigation_timeout_ms: int = 20000
    max_content_chars: int = 4000
    max_links_returned: int = 15
    max_pages_per_browse: int = 3
    search_engine: str = "duckduckgo"
    user_data_dir: str = ""
    """Directory for persistent browser profile (cookies, localStorage,
    login sessions).  When set, uses Playwright's persistent context so
    the agent stays logged in across sessions.  When empty, uses an
    ephemeral context that forgets everything on shutdown."""
    cdp_url: str = ""
    """CDP endpoint URL (e.g. 'http://127.0.0.1:9245').  When set,
    Playwright connects to an existing browser (the Electron app's
    webview) via Chrome DevTools Protocol instead of launching its own
    Chromium.  This keeps the browser embedded in the desktop app and
    avoids bot-detection fingerprints."""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BrowserConfig:
        """Create from a config dict (e.g., runtime.json tools.browser section)."""
        return cls(
            headless=d.get("headless", True),
            channel=d.get("channel", ""),
            timeout_ms=d.get("timeout_ms", 15000),
            navigation_timeout_ms=d.get("navigation_timeout_ms", 20000),
            max_content_chars=d.get("max_content_chars", 4000),
            max_links_returned=d.get("max_links_returned", 15),
            max_pages_per_browse=d.get("max_pages_per_browse", 3),
            search_engine=d.get("search_engine", "duckduckgo"),
            user_data_dir=d.get("user_data_dir", ""),
            cdp_url=d.get("cdp_url", ""),
            user_agent=d.get("user_agent", cls.user_agent),
        )


# ---------------------------------------------------------------------------
# Search Result
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """A single web search result."""

    title: str = ""
    url: str = ""
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass
class PageContent:
    """Content extracted from a web page."""

    url: str = ""
    title: str = ""
    text: str = ""
    links: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text[:500] + "..." if len(self.text) > 500 else self.text,
            "n_links": len(self.links),
        }


@dataclass
class SnapshotElement:
    """A single element from an AI accessibility snapshot.

    Each interactive element on the page gets a ref ID like ``e3`` that
    the agent uses for actions: ``click e3``, ``fill e5 "hello"``.
    """

    ref: str  # e.g., "e3"
    role: str  # e.g., "button", "textbox", "link"
    name: str  # accessible name (label, text content)
    value: str = ""  # current value (for inputs)
    disabled: bool = False
    focused: bool = False

    def to_line(self) -> str:
        """Format as a single line for the agent snapshot."""
        parts = [f"[ref={self.ref}]", self.role]
        if self.name:
            parts.append(f'"{self.name}"')
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.disabled:
            parts.append("(disabled)")
        if self.focused:
            parts.append("(focused)")
        return " ".join(parts)


@dataclass
class PageSnapshot:
    """AI snapshot of a page: accessibility tree + metadata.

    This is what the agent sees instead of raw HTML.  Each interactive
    element gets a ref ID for click/fill/type actions.
    """

    url: str = ""
    title: str = ""
    elements: list[SnapshotElement] = field(default_factory=list)
    text_summary: str = ""  # non-interactive text content summary

    @property
    def interactive_text(self) -> str:
        """Format as a readable text block for injection into context."""
        lines = [f"Page: {self.title}", f"URL: {self.url}", ""]

        if self.elements:
            lines.append("Interactive elements:")
            for el in self.elements:
                lines.append(f"  {el.to_line()}")
            lines.append("")

        if self.text_summary:
            lines.append("Page content:")
            lines.append(self.text_summary)

        return "\n".join(lines)

    @property
    def element_count(self) -> int:
        return len(self.elements)


@dataclass
class ActionResult:
    """Result of an interactive browser action (click, fill, etc.)."""

    success: bool = True
    message: str = ""
    snapshot: PageSnapshot | None = None  # page state after the action
    screenshot_path: str = ""  # if a screenshot was taken
    error: str = ""

    @property
    def text(self) -> str:
        parts = []
        if self.success:
            parts.append(self.message or "Action completed.")
        else:
            parts.append(f"Action failed: {self.error}")

        if self.snapshot:
            parts.append("")
            parts.append(self.snapshot.interactive_text)

        return "\n".join(parts)


@dataclass
class BrowseResult:
    """Result of a browsing session (search + read pages)."""

    query: str = ""
    search_results: list[SearchResult] = field(default_factory=list)
    pages_read: list[PageContent] = field(default_factory=list)
    success: bool = False
    error: str = ""

    # Evaluation metadata (populated by the agentic browse loop)
    pages_skipped: int = 0
    """How many search results were read but deemed irrelevant."""
    pages_evaluated: int = 0
    """Total results read (accepted + skipped)."""
    used_fallback: bool = False
    """True if no result was relevant and we fell back to the top-ranked one."""

    @property
    def relevance_ratio(self) -> float:
        """Fraction of evaluated pages that were accepted (0.0 – 1.0)."""
        if self.pages_evaluated == 0:
            return 0.0
        return (self.pages_evaluated - self.pages_skipped) / self.pages_evaluated

    @property
    def full_text(self) -> str:
        """Combined text from all pages read, for injection into history."""
        parts = []
        if self.search_results:
            parts.append(f"Search results for: {self.query}\n")
            for i, r in enumerate(self.search_results[:5], 1):
                parts.append(f"{i}. {r.title}")
                if r.snippet:
                    parts.append(f"   {r.snippet}")
                parts.append("")

        for page in self.pages_read:
            parts.append(f"--- Reading: {page.title or page.url} ---\n")
            parts.append(page.text)
            parts.append("")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# BrowserEngine
# ---------------------------------------------------------------------------


class BrowserEngine:
    """Persistent Playwright-backed headless browser.

    Lazily initializes a Chromium browser on first use. The browser
    context persists across calls for session continuity (cookies,
    localStorage, etc.). Thread-safe via a lock.

    Usage:
        engine = BrowserEngine(config)
        result = engine.search("what is consciousness")
        page = engine.read_page("https://en.wikipedia.org/wiki/Consciousness")
        browse = engine.browse("consciousness philosophy", depth=2)
        engine.shutdown()
    """

    def __init__(
        self,
        config: BrowserConfig | None = None,
        on_navigation: Any | None = None,
    ):
        self.config = config or BrowserConfig()
        self._lock = threading.Lock()
        self._on_navigation = on_navigation

        # Playwright objects (lazy init)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._initialized = False
        self._available = True  # Set to False if Playwright import fails

        self._cdp_mode = False  # True when connected via CDP to Electron

        # Dedicated Playwright thread -- Playwright's sync API uses
        # greenlets that are bound to the OS thread that created them.
        # All Playwright operations (init, page reads, shutdown) MUST
        # run on this single thread to avoid greenlet cross-thread errors.
        self._pw_executor: Any = None  # ThreadPoolExecutor(max_workers=1)
        self._init_thread_id: int | None = None  # Thread that owns Playwright

    # ===================================================================
    # Lifecycle
    # ===================================================================

    @staticmethod
    def _is_in_async_loop() -> bool:
        """Check if we're inside a running asyncio event loop."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return loop is not None
        except RuntimeError:
            return False

    def _ensure_initialized(self) -> bool:
        """Lazily start the browser. Returns True if ready.

        ALWAYS initializes Playwright on a dedicated single-worker thread
        to guarantee that all subsequent operations (page reads, shutdown)
        run on the same OS thread.  Playwright's sync API uses greenlets
        bound to their creating thread -- mixing threads causes
        'Cannot switch to a different thread' errors.
        """
        if self._initialized:
            return True

        if not self._available:
            return False

        # Step 1: Import check
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError as e:
            logger.warning("BrowserEngine: playwright package not importable (%s)", e)
            self._available = False
            return False

        # Step 2: Always launch on the dedicated Playwright thread
        # so all subsequent ops can safely proxy through it.
        return self._init_in_thread()

    def _do_init(self) -> bool:
        """Actually launch Playwright/Chromium (must be called outside async loop).

        Three modes (checked in order):
        1. **CDP mode** (``cdp_url`` set): Connect to an existing browser
           (the Electron app's webview) via Chrome DevTools Protocol.
           No new browser is launched -- Playwright drives the in-app panel.
        2. **Persistent mode** (``user_data_dir`` set): Launch Chromium with
           a persistent profile so cookies/logins survive restarts.
        3. **Ephemeral mode** (default): Launch a fresh Chromium instance.
        """
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()

            # ----- CDP mode: connect to Electron's webview -----
            if self.config.cdp_url:
                return self._init_cdp_mode()

            # ----- Standalone mode: launch our own Chromium -----
            launch_kwargs: dict[str, Any] = {
                "headless": self.config.headless,
                "args": ["--window-size=1080,760", "--window-position=80,60"],
            }
            if self.config.channel:
                launch_kwargs["channel"] = self.config.channel

            context_kwargs: dict[str, Any] = {
                "user_agent": self.config.user_agent,
                "viewport": {"width": 1080, "height": 700},
                "locale": "en-US",
                "timezone_id": "UTC",
            }

            if self.config.user_data_dir:
                Path(self.config.user_data_dir).mkdir(parents=True, exist_ok=True)
                self._browser = None
                self._context = self._playwright.chromium.launch_persistent_context(
                    self.config.user_data_dir,
                    **launch_kwargs,
                    **context_kwargs,
                )
                logger.info(
                    "BrowserEngine: using persistent profile at %s",
                    self.config.user_data_dir,
                )
            else:
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
                self._context = self._browser.new_context(**context_kwargs)

            self._context.set_default_timeout(self.config.timeout_ms)
            self._context.set_default_navigation_timeout(
                self.config.navigation_timeout_ms
            )

            # Only block heavy resources in headless mode.
            # In visible mode, let the user see the full page.
            if self.config.headless:
                self._context.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type
                        in ("image", "media", "font", "stylesheet")
                        else route.continue_()
                    ),
                )

            self._initialized = True
            self._init_thread_id = threading.get_ident()
            logger.info(
                "BrowserEngine: started (headless=%s, channel=%s, persistent=%s, thread=%d)",
                self.config.headless,
                self.config.channel or "bundled-chromium",
                bool(self.config.user_data_dir),
                self._init_thread_id,
            )
            return True

        except Exception as e:
            logger.error(
                "BrowserEngine: Failed to start browser: %s: %s -- "
                "web browsing will use urllib fallback",
                type(e).__name__, e,
            )
            return False

    # URLs that must never be driven by the agent (Electron internals).
    _CDP_SKIP_URLS = (
        "localhost:4200", "localhost:9222",
        "devtools://", "chrome://", "chrome-extension://",
        "about:devtools",
    )

    def _init_cdp_mode(self) -> bool:
        """Connect to Electron via CDP and find the webview page.

        If no webview target is available (user hasn't opened the browser
        panel, or the webview isn't exposed via CDP), we **fall back** to
        launching a standalone Playwright Chromium so the agent always has
        a working browser.
        """
        import time as _time

        cdp_url = self.config.cdp_url
        logger.info("BrowserEngine: connecting via CDP to %s", cdp_url)

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:
                last_err = exc
                logger.debug(
                    "BrowserEngine: CDP connect attempt %d failed: %s",
                    attempt + 1, exc,
                )
                _time.sleep(1.0)
        else:
            logger.warning(
                "BrowserEngine: CDP connection failed after 3 attempts: %s "
                "-- falling back to standalone browser",
                last_err,
            )
            return self._init_standalone()

        all_pages: list[tuple[Any, Any]] = []
        for ctx in self._browser.contexts:
            for page in ctx.pages:
                all_pages.append((ctx, page))

        logger.info(
            "BrowserEngine: CDP found %d context(s), %d page(s): %s",
            len(self._browser.contexts),
            len(all_pages),
            [p.url for _, p in all_pages],
        )

        target_page = None
        self._context = None
        for ctx, page in all_pages:
            url = page.url
            if any(skip in url for skip in self._CDP_SKIP_URLS):
                continue
            if "index.html" in url and ("file://" in url or "renderer" in url):
                continue
            target_page = page
            self._context = ctx
            break

        if not target_page:
            logger.warning(
                "BrowserEngine: no webview page found via CDP "
                "(only Electron internals) -- falling back to standalone browser",
            )
            try:
                self._browser.close()
            except Exception:
                pass
            return self._init_standalone()

        self._active_page = target_page
        logger.info(
            "BrowserEngine: attached to webview page: %s",
            target_page.url,
        )

        self._context.set_default_timeout(self.config.timeout_ms)
        self._context.set_default_navigation_timeout(
            self.config.navigation_timeout_ms
        )
        self._apply_stealth(self._context)

        self._initialized = True
        self._init_thread_id = threading.get_ident()
        self._cdp_mode = True
        logger.info(
            "BrowserEngine: CDP mode active (thread=%d, contexts=%d, pages=%d)",
            self._init_thread_id,
            len(self._browser.contexts),
            sum(len(c.pages) for c in self._browser.contexts),
        )
        return True

    def _init_standalone(self) -> bool:
        """Launch a standalone Playwright Chromium (fallback when CDP has no webview)."""
        logger.info("BrowserEngine: launching standalone Chromium")

        launch_kwargs: dict[str, Any] = {
            "headless": False,
            "args": ["--window-size=1080,760", "--window-position=80,60"],
        }
        if self.config.channel:
            launch_kwargs["channel"] = self.config.channel

        context_kwargs: dict[str, Any] = {
            "user_agent": self.config.user_agent,
            "viewport": {"width": 1080, "height": 700},
            "locale": "en-US",
            "timezone_id": "UTC",
        }

        user_data_dir = self.config.user_data_dir
        if not user_data_dir:
            import tempfile
            user_data_dir = str(
                Path(tempfile.gettempdir()) / "nls-browser-profile"
            )

        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        self._browser = None
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir,
            **launch_kwargs,
            **context_kwargs,
        )
        logger.info(
            "BrowserEngine: standalone browser with persistent profile at %s",
            user_data_dir,
        )

        self._context.set_default_timeout(self.config.timeout_ms)
        self._context.set_default_navigation_timeout(
            self.config.navigation_timeout_ms
        )
        self._apply_stealth(self._context)

        self._initialized = True
        self._init_thread_id = threading.get_ident()
        self._cdp_mode = False
        return True

    def _needs_thread_proxy(self) -> bool:
        """Check if Playwright ops need to be proxied to the init thread.

        Playwright's sync API uses greenlets bound to the OS thread that
        created them.  If the current thread is NOT the init thread, we
        must proxy the call through _run_on_pw_thread to avoid the
        'Cannot switch to a different thread' greenlet error.

        When _init_thread_id is unknown, we conservatively return True
        to force proxying -- safer than risking a greenlet crash.
        """
        if self._init_thread_id is None:
            return True
        return threading.get_ident() != self._init_thread_id

    def _get_pw_executor(self) -> Any:
        """Get or create the dedicated Playwright thread pool.

        A single-worker ThreadPoolExecutor ensures that ALL Playwright
        operations (init, page reads, shutdown) run on the SAME OS
        thread, which is required because Playwright's sync API uses
        greenlets that are bound to their creating thread.
        """
        if self._pw_executor is None:
            import concurrent.futures
            self._pw_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="playwright",
            )
        return self._pw_executor

    def _run_on_pw_thread(self, fn: Any, *args: Any, timeout: float = 60) -> Any:
        """Submit a callable to the dedicated Playwright thread and wait."""
        executor = self._get_pw_executor()
        future = executor.submit(fn, *args)
        return future.result(timeout=timeout)

    def _init_in_thread(self) -> bool:
        """Launch Playwright on the dedicated single-worker thread.

        All Playwright operations (init, page reads, shutdown) run on
        this thread to avoid greenlet cross-thread errors.
        """
        logger.info("BrowserEngine: launching Playwright on dedicated thread")

        try:
            return self._run_on_pw_thread(self._do_init, timeout=30)
        except Exception as e:
            logger.error(
                "BrowserEngine: thread-based init failed: %s: %s",
                type(e).__name__, e,
            )
            return False

    def shutdown(self) -> None:
        """Close the browser and clean up Playwright resources."""
        with self._lock:
            def _do_shutdown() -> None:
                # In CDP mode we only disconnect -- never close the browser
                # because it belongs to the Electron app.
                if self._cdp_mode:
                    if self._browser is not None:
                        try:
                            self._browser.close()  # disconnects, doesn't kill
                        except Exception:
                            pass
                    if self._playwright is not None:
                        try:
                            self._playwright.stop()
                        except Exception:
                            pass
                    return

                if self._context is not None:
                    try:
                        self._context.close()
                    except Exception:
                        pass

                if self._browser is not None:
                    try:
                        self._browser.close()
                    except Exception:
                        pass

                if self._playwright is not None:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass

            # Run cleanup on the Playwright thread if it exists,
            # since Playwright objects are bound to that thread.
            if self._pw_executor is not None:
                try:
                    self._run_on_pw_thread(_do_shutdown, timeout=10)
                except Exception:
                    pass
                self._pw_executor.shutdown(wait=False)
                self._pw_executor = None
            else:
                _do_shutdown()

            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
            logger.info("BrowserEngine: shutdown complete")

    @property
    def is_available(self) -> bool:
        """Check if Playwright is available (without starting it)."""
        if not self._available:
            return False
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            self._available = False
            return False

    # ===================================================================
    # Core Operations
    # ===================================================================

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web via DuckDuckGo and return structured results.

        Uses urllib for the search query (reliable, no bot detection)
        and parses the DDG HTML results page. Playwright is reserved
        for reading actual web pages where JS rendering matters.

        This hybrid approach is deliberate:
          - Search engines aggressively block headless browsers
          - DDG's HTML endpoint is designed for lightweight clients
          - Playwright adds value for reading destination pages, not search
        """
        try:
            return self._urllib_search(query, max_results)
        except Exception as e:
            logger.error("BrowserEngine.search failed: %s", e)
            return []

    def _urllib_search(
        self, query: str, max_results: int
    ) -> list[SearchResult]:
        """Search DuckDuckGo HTML endpoint via urllib."""
        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"

        req = urllib.request.Request(url, headers={
            "User-Agent": self.config.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        })

        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Parse DDG HTML results
        results: list[SearchResult] = []

        # DDG uses <a class="result__a"> for titles and
        # <a class="result__snippet"> for snippets
        _DDG_RESULT_RE = re.compile(
            r'<a\s+[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
            r'.*?'
            r'(?:<a\s+[^>]*class="result__snippet"[^>]*>(.*?)</a>)?',
            re.DOTALL,
        )

        for match in _DDG_RESULT_RE.finditer(html):
            href = match.group(1)
            title_html = match.group(2)
            snippet_html = match.group(3) or ""

            # Strip HTML tags from title and snippet
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()

            # Resolve DDG redirect URL to real URL
            real_url = self._resolve_ddg_url(href)

            # Skip ads and tracking URLs
            if not real_url or "ad_provider" in real_url or "/y.js?" in real_url:
                continue

            if title and real_url:
                results.append(SearchResult(
                    title=title,
                    url=real_url,
                    snippet=snippet,
                ))

            if len(results) >= max_results:
                break

        logger.info(
            "BrowserEngine: search '%s' returned %d results", query, len(results)
        )
        return results

    def read_page(self, url: str) -> PageContent:
        """Navigate to a URL and extract readable content.

        Playwright renders JavaScript, giving us actual content from
        SPAs and dynamic pages.  All Playwright operations are proxied
        to the dedicated init thread to avoid greenlet cross-thread
        errors.  Falls back to urllib if Playwright is unavailable.
        """
        with self._lock:
            if not self._ensure_initialized():
                return self._fallback_read_page(url)

            # Always proxy through the Playwright thread unless we're
            # already on it (shouldn't normally happen from outside).
            if self._needs_thread_proxy():
                return self._read_page_in_thread(url)

            # We're on the Playwright thread already (rare edge case)
            try:
                page = self._context.new_page()
                try:
                    return self._browser_read_page(page, url)
                finally:
                    page.close()
            except Exception as e:
                logger.error("BrowserEngine.read_page failed for %s: %s", url, e)
                return self._fallback_read_page(url)

    def _read_page_in_thread(self, url: str) -> PageContent:
        """Run Playwright page reading on the dedicated Playwright thread.

        Uses the same persistent thread that initialized Playwright,
        avoiding greenlet cross-thread errors.
        """
        def _do_read() -> PageContent:
            try:
                page = self._context.new_page()
                try:
                    return self._browser_read_page(page, url)
                finally:
                    page.close()
            except Exception as e:
                logger.error("BrowserEngine.read_page (threaded) failed for %s: %s", url, e)
                return self._fallback_read_page(url)

        try:
            timeout = self.config.navigation_timeout_ms / 1000 + 10
            return self._run_on_pw_thread(_do_read, timeout=timeout)
        except Exception as e:
            logger.error("BrowserEngine: threaded read_page timed out for %s: %s", url, e)
            return self._fallback_read_page(url)

    def _browser_read_page(self, page: Any, url: str) -> PageContent:
        """Read a page using the Playwright browser."""
        page.goto(url, wait_until="domcontentloaded")

        # Wait a bit for dynamic content to load
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # Timeout is OK -- we got domcontentloaded already

        title = page.title() or ""
        final_url = page.url

        # Extract readable text using smart selectors
        text = self._extract_readable_text(page)

        # Extract links for potential follow-up browsing
        links = self._extract_links(page)

        content = PageContent(
            url=final_url,
            title=title,
            text=text[:self.config.max_content_chars],
            links=links[:self.config.max_links_returned],
        )

        logger.info(
            "BrowserEngine: read '%s' -> %d chars, %d links",
            title[:50], len(content.text), len(content.links),
        )
        return content

    def browse(
        self,
        query: str,
        depth: int = 1,
        max_pages: int | None = None,
    ) -> BrowseResult:
        """Full browsing session: search, rank, evaluate, iterate.

        Implements an agentic browse loop:
        1. Search for the query
        2. Rank results by relevance (title/snippet vs query terms)
        3. Read the best-ranked result, evaluate if the content is
           actually relevant — if not, skip it and try the next one
        4. Once ``depth`` good pages are found (or all results exhausted),
           optionally follow interesting links from the best page

        Args:
            query: Search query
            depth: How many *good* search results to read (default 1)
            max_pages: Total max pages to read including followed links.
                       Defaults to config.max_pages_per_browse.
        """
        if max_pages is None:
            max_pages = self.config.max_pages_per_browse

        result = BrowseResult(query=query)

        try:
            # Step 1: Search
            search_results = self.search(query)
            result.search_results = search_results

            if not search_results:
                result.error = "No search results found"
                return result

            # Step 2: Rank results by relevance to query
            ranked = self._rank_results(search_results, query)

            # Step 3: Read results in ranked order, evaluate each
            pages_read = 0
            pages_skipped = 0
            pages_evaluated = 0
            for sr in ranked:
                if pages_read >= max_pages or pages_read >= depth:
                    break

                page_content = self.read_page(sr.url)
                if not page_content.text:
                    continue

                pages_evaluated += 1
                if self._is_relevant_content(page_content.text, query):
                    result.pages_read.append(page_content)
                    pages_read += 1
                    logger.info(
                        "BrowserEngine: accepted '%s' for query '%s'",
                        sr.title[:60], query[:60],
                    )
                else:
                    pages_skipped += 1
                    logger.info(
                        "BrowserEngine: skipped '%s' (not relevant to '%s')",
                        sr.title[:60], query[:60],
                    )

            result.pages_skipped = pages_skipped
            result.pages_evaluated = pages_evaluated

            if pages_skipped and not pages_read:
                # All ranked results were irrelevant — fall back to the
                # top-ranked page anyway so the caller gets *something*
                best = ranked[0]
                page_content = self.read_page(best.url)
                if page_content.text:
                    result.pages_read.append(page_content)
                    result.used_fallback = True
                    pages_read += 1
                    logger.info(
                        "BrowserEngine: fallback to top-ranked '%s' "
                        "(all %d results seemed irrelevant)",
                        best.title[:60], pages_skipped,
                    )

            # Step 4: Follow interesting links (if budget remains)
            if pages_read < max_pages and result.pages_read:
                first_page = result.pages_read[0]
                for link in first_page.links:
                    if pages_read >= max_pages:
                        break

                    link_url = link.get("url", "")
                    link_text = link.get("text", "")

                    if self._is_content_link(link_url, link_text, query):
                        page_content = self.read_page(link_url)
                        if (
                            page_content.text
                            and len(page_content.text) > 200
                            and self._is_relevant_content(
                                page_content.text, query,
                            )
                        ):
                            result.pages_read.append(page_content)
                            pages_read += 1

            result.success = len(result.pages_read) > 0

        except Exception as e:
            result.error = str(e)
            logger.error("BrowserEngine.browse failed: %s", e)

        return result

    # -------------------------------------------------------------------
    # Result ranking & relevance evaluation
    # -------------------------------------------------------------------

    _STOPWORDS: set[str] = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "of", "in", "to", "for", "with", "on", "at", "from", "by",
        "about", "as", "into", "through", "during", "before", "after",
        "and", "but", "or", "nor", "not", "so", "yet", "both",
        "what", "which", "who", "whom", "this", "that", "these",
        "those", "it", "its", "how", "why", "when", "where",
        "most", "more", "very", "just", "also", "than",
    }

    def _rank_results(
        self,
        results: list[SearchResult],
        query: str,
    ) -> list[SearchResult]:
        """Rank search results by relevance to the query.

        Scores each result by how many query terms appear in its title
        (weighted 3x) and snippet (weighted 1x).  Returns a new list
        sorted by descending score, preserving original order as tiebreak.
        """
        query_terms = {
            t for t in query.lower().split()
            if t not in self._STOPWORDS and len(t) > 2
        }
        if not query_terms:
            return list(results)

        scored: list[tuple[float, int, SearchResult]] = []
        for idx, sr in enumerate(results):
            title_lower = (sr.title or "").lower()
            snippet_lower = (sr.snippet or "").lower()

            title_hits = sum(1 for t in query_terms if t in title_lower)
            snippet_hits = sum(1 for t in query_terms if t in snippet_lower)
            score = title_hits * 3.0 + snippet_hits * 1.0

            scored.append((score, idx, sr))

        scored.sort(key=lambda x: (-x[0], x[1]))

        if scored and scored[0][2] is not results[0] and scored[0][0] > 0:
            logger.info(
                "BrowserEngine: re-ranked results for '%s' — "
                "best match: '%s' (score %.1f)",
                query[:50],
                scored[0][2].title[:50],
                scored[0][0],
            )

        return [sr for _, _, sr in scored]

    def _is_relevant_content(
        self,
        text: str,
        query: str,
        min_hit_ratio: float = 0.3,
    ) -> bool:
        """Check if page content is actually relevant to the query.

        Extracts meaningful terms from the query (skipping stopwords)
        and checks how many appear in the page text.  Requires at least
        ``min_hit_ratio`` of query terms to be present, with a floor
        of 1 term.
        """
        query_terms = [
            t for t in query.lower().split()
            if t not in self._STOPWORDS and len(t) > 2
        ]
        if not query_terms:
            return True

        text_lower = text[:5000].lower()
        hits = sum(1 for t in query_terms if t in text_lower)
        required = max(1, int(len(query_terms) * min_hit_ratio))
        return hits >= required

    # ===================================================================
    # Interactive Mode -- Persistent page with AI snapshots
    # ===================================================================

    def navigate(self, url: str) -> ActionResult:
        """Navigate the active page to a URL and return an AI snapshot.

        Creates a persistent page on first call that stays open for
        subsequent click/fill/snapshot actions (unlike read_page which
        opens and closes a page each time).
        """
        # Emit navigation event so the frontend knows
        if self._on_navigation:
            try:
                self._on_navigation("navigate", url, "")
            except Exception:
                pass

        with self._lock:
            if not self._ensure_initialized():
                return ActionResult(
                    success=False,
                    error="Browser engine not available (Playwright not installed)",
                )

            def _do_navigate() -> ActionResult:
                try:
                    page = self._get_or_create_active_page()
                    page.goto(url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass

                    self._dismiss_cookie_banner(page)

                    snapshot = self._take_snapshot(page)

                    if self._on_navigation:
                        try:
                            self._on_navigation(
                                "loaded", page.url, snapshot.title,
                            )
                        except Exception:
                            pass

                    return ActionResult(
                        success=True,
                        message=f"Navigated to: {snapshot.title or url}",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.navigate failed for %s: %s", url, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_navigate, timeout=30)
            return _do_navigate()

    def click(self, ref: str) -> ActionResult:
        """Click an element by its accessibility ref (e.g., 'e3').

        Uses the ref from a previous snapshot to identify the element.
        Returns a fresh snapshot of the page after clicking.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_click() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    locator = self._resolve_ref(page, ref)
                    if locator is None:
                        return ActionResult(
                            success=False,
                            error=f"Element ref '{ref}' not found. Take a new snapshot.",
                        )

                    locator.click(timeout=self.config.timeout_ms)

                    # Wait for any navigation or dynamic content
                    try:
                        page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Clicked [{ref}]",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.click(%s) failed: %s", ref, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_click, timeout=20)
            return _do_click()

    def fill(self, ref: str, value: str) -> ActionResult:
        """Fill a text input by its accessibility ref.

        Clears the existing value and types the new one.
        Returns a fresh snapshot after filling.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_fill() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    locator = self._resolve_ref(page, ref)
                    if locator is None:
                        return ActionResult(
                            success=False,
                            error=f"Element ref '{ref}' not found. Take a new snapshot.",
                        )

                    locator.fill(value, timeout=self.config.timeout_ms)

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Filled [{ref}] with: {value[:50]}{'...' if len(value) > 50 else ''}",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.fill(%s) failed: %s", ref, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_fill, timeout=15)
            return _do_fill()

    def type_text(self, ref: str, text: str, submit: bool = False) -> ActionResult:
        """Type text into an element character by character (appends, doesn't clear).

        Useful for search boxes and inputs where fill() might not trigger
        autocomplete or other JS event handlers properly.

        If ``submit`` is True, presses Enter after typing.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_type() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    locator = self._resolve_ref(page, ref)
                    if locator is None:
                        return ActionResult(
                            success=False,
                            error=f"Element ref '{ref}' not found. Take a new snapshot.",
                        )

                    locator.type(text, timeout=self.config.timeout_ms)

                    if submit:
                        locator.press("Enter")
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Typed into [{ref}]: {text[:50]}{'...' if len(text) > 50 else ''}"
                                + (" (submitted)" if submit else ""),
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.type_text(%s) failed: %s", ref, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_type, timeout=20)
            return _do_type()

    def screenshot(self, output_path: str | None = None, full_page: bool = False) -> ActionResult:
        """Take a screenshot of the active page.

        Returns the path to the saved PNG file.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_screenshot() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    if not output_path:
                        import tempfile
                        path = Path(tempfile.mkdtemp()) / "screenshot.png"
                    else:
                        path = Path(output_path)
                        path.parent.mkdir(parents=True, exist_ok=True)

                    page.screenshot(path=str(path), full_page=full_page)

                    return ActionResult(
                        success=True,
                        message=f"Screenshot saved to: {path}",
                        screenshot_path=str(path),
                    )
                except Exception as e:
                    logger.error("BrowserEngine.screenshot failed: %s", e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_screenshot, timeout=15)
            return _do_screenshot()

    def capture_frame(self) -> Any | None:
        """Capture the active page as a PIL Image for the visual cortex.

        Returns None if the browser is not initialized or capture fails.
        Uses Playwright's screenshot-to-bytes (no disk I/O) for speed.
        """
        if not self._initialized:
            return None

        def _do_capture():
            try:
                page = self._get_active_page()
                if page is None:
                    return None
                png_bytes = page.screenshot(type="jpeg", quality=60)
                from PIL import Image
                import io as _io
                return Image.open(_io.BytesIO(png_bytes)).convert("RGB")
            except Exception:
                return None

        try:
            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_capture, timeout=5)
            return _do_capture()
        except Exception:
            return None

    def evaluate(self, script: str) -> ActionResult:
        """Execute JavaScript in the active page and return the result."""
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_evaluate() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    result = page.evaluate(script)

                    if isinstance(result, (dict, list)):
                        result_text = json.dumps(result, indent=2, ensure_ascii=False)
                    else:
                        result_text = str(result) if result is not None else "(undefined)"

                    return ActionResult(
                        success=True,
                        message=f"JS result: {result_text[:2000]}",
                    )
                except Exception as e:
                    logger.error("BrowserEngine.evaluate failed: %s", e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_evaluate, timeout=15)
            return _do_evaluate()

    def get_snapshot(self) -> ActionResult:
        """Take an AI snapshot of the current active page without navigating."""
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_snapshot() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Snapshot: {snapshot.element_count} interactive elements",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.get_snapshot failed: %s", e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_snapshot, timeout=15)
            return _do_snapshot()

    def press_key(self, key: str) -> ActionResult:
        """Press a keyboard key on the active page (e.g., 'Enter', 'Escape', 'Tab')."""
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_press() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    page.keyboard.press(key)

                    try:
                        page.wait_for_load_state("networkidle", timeout=2000)
                    except Exception:
                        pass

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Pressed key: {key}",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.press_key(%s) failed: %s", key, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_press, timeout=10)
            return _do_press()

    def select_option(self, ref: str, value: str) -> ActionResult:
        """Select an option in a dropdown/combobox by its ref.

        Tries matching by value, then by label text.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_select() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    locator = self._resolve_ref(page, ref)
                    if locator is None:
                        return ActionResult(
                            success=False,
                            error=f"Element ref '{ref}' not found. Take a new snapshot.",
                        )

                    locator.select_option(value, timeout=self.config.timeout_ms)

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Selected '{value}' in [{ref}]",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.select_option(%s) failed: %s", ref, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_select, timeout=15)
            return _do_select()

    def wait_for(self, text: str = "", timeout_ms: int = 10000) -> ActionResult:
        """Wait for text to appear on the page, then take a snapshot.

        Useful after clicking a button that triggers dynamic content.
        """
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_wait() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    if text:
                        page.wait_for_selector(
                            f"text={text}", timeout=timeout_ms,
                        )
                    else:
                        try:
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                        except Exception:
                            pass

                    snapshot = self._take_snapshot(page)
                    detail = ': found text "{}"'.format(text[:50]) if text else ''
                    return ActionResult(
                        success=True,
                        message=f"Wait complete{detail}",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.wait_for failed: %s", e)
                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=False,
                        error=f"Timeout waiting for '{text}': {e}",
                        snapshot=snapshot,
                    )

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_wait, timeout=max(15, timeout_ms / 1000 + 5))
            return _do_wait()

    def hover(self, ref: str) -> ActionResult:
        """Hover over an element by its accessibility ref."""
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_hover() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    locator = self._resolve_ref(page, ref)
                    if locator is None:
                        return ActionResult(
                            success=False,
                            error=f"Element ref '{ref}' not found. Take a new snapshot.",
                        )

                    locator.hover(timeout=self.config.timeout_ms)

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Hovered [{ref}]",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.hover(%s) failed: %s", ref, e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_hover, timeout=10)
            return _do_hover()

    def scroll(self, direction: str = "down", amount: int = 500) -> ActionResult:
        """Scroll the active page in the given direction."""
        with self._lock:
            if not self._initialized:
                return ActionResult(success=False, error="Browser not initialized. Navigate first.")

            def _do_scroll() -> ActionResult:
                try:
                    page = self._get_active_page()
                    if page is None:
                        return ActionResult(success=False, error="No active page. Navigate first.")

                    delta_y = -amount if direction == "up" else amount
                    page.mouse.wheel(0, delta_y)
                    page.wait_for_timeout(300)

                    snapshot = self._take_snapshot(page)
                    return ActionResult(
                        success=True,
                        message=f"Scrolled {direction} {amount}px",
                        snapshot=snapshot,
                    )
                except Exception as e:
                    logger.error("BrowserEngine.scroll failed: %s", e)
                    return ActionResult(success=False, error=str(e))

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_scroll, timeout=10)
            return _do_scroll()

    def close_active_page(self) -> ActionResult:
        """Close the active page (ends the interactive session).

        In CDP mode the page belongs to Electron's webview, so we navigate
        it to about:blank instead of closing it.
        """
        with self._lock:
            def _do_close() -> ActionResult:
                if hasattr(self, "_active_page") and self._active_page is not None:
                    if self._cdp_mode:
                        try:
                            self._active_page.goto("about:blank")
                        except Exception:
                            pass
                        self._snapshot_refs.clear()
                        return ActionResult(success=True, message="Browser reset to blank.")
                    try:
                        self._active_page.close()
                    except Exception:
                        pass
                    self._active_page = None
                    self._snapshot_refs.clear()
                    return ActionResult(success=True, message="Active page closed.")
                return ActionResult(success=True, message="No active page to close.")

            if self._needs_thread_proxy():
                return self._run_on_pw_thread(_do_close, timeout=5)
            return _do_close()

    # ===================================================================
    # Stealth patches (anti-bot-detection)
    # ===================================================================

    _STEALTH_JS = """
    // Remove webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Consistent plugins array (empty in headless, populated in real browsers)
    if (navigator.plugins.length === 0) {
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
    }

    // Consistent languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    // Remove automation-related Chrome properties
    if (window.chrome) {
        window.chrome.runtime = window.chrome.runtime || {};
    } else {
        window.chrome = { runtime: {} };
    }

    // Fix permissions query for notifications
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    """

    def _apply_stealth(self, context: Any) -> None:
        """Inject stealth scripts into every new page in the context."""
        try:
            context.add_init_script(self._STEALTH_JS)
            # Also inject into any existing pages
            for page in context.pages:
                try:
                    page.evaluate(self._STEALTH_JS)
                except Exception:
                    pass
            logger.debug("BrowserEngine: stealth patches applied")
        except Exception as exc:
            logger.debug("BrowserEngine: stealth injection failed: %s", exc)

    # ===================================================================
    # Cookie banner auto-dismissal
    # ===================================================================

    _COOKIE_DISMISS_SELECTORS = [
        # Common consent buttons — ordered by specificity
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('Agree')",
        "button:has-text('I agree')",
        "button:has-text('Allow all')",
        "button:has-text('Allow All')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "button:has-text('Accepteren')",
        "button:has-text('Akkoord')",
        "button:has-text('Accetta tutti')",
        "button:has-text('Accetta')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Tout accepter')",
        # ARIA-based
        "[aria-label*='accept' i][role='button']",
        "[aria-label*='consent' i][role='button']",
        "[aria-label*='cookie' i][role='button']",
        # Common framework IDs/classes
        "#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#accept-cookie-notification",
        "[data-testid='cookie-policy-dialog-accept-button']",
        ".cookie-consent-accept",
        ".js-accept-cookies",
        "#gdpr-consent-accept",
        ".consent-accept",
    ]

    def _dismiss_cookie_banner(self, page: Any) -> None:
        """Try to dismiss cookie/consent banners after page load.

        Iterates through common cookie banner selectors and clicks the
        first visible one.  Errors are silently ignored since banners
        vary wildly across sites and this is best-effort.
        """
        for selector in self._COOKIE_DISMISS_SELECTORS:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=600):
                    btn.click(timeout=2000)
                    logger.debug("Dismissed cookie banner via: %s", selector)
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    return
            except Exception:
                continue

    # ===================================================================
    # Active page management (persistent page for multi-step sessions)
    # ===================================================================

    def _get_or_create_active_page(self) -> Any:
        """Get the persistent active page, creating it if needed."""
        if not hasattr(self, "_active_page") or self._active_page is None:
            self._active_page = self._context.new_page()
            self._snapshot_refs: dict[str, dict[str, Any]] = {}
            logger.debug("Created new active page for interactive session")
        return self._active_page

    def _get_active_page(self) -> Any | None:
        """Get the active page (None if no session started)."""
        return getattr(self, "_active_page", None)

    # ===================================================================
    # AI Snapshot -- Accessibility tree with semantic refs
    # ===================================================================

    def _take_snapshot(self, page: Any) -> PageSnapshot:
        """Take an AI snapshot: accessibility tree with ref IDs.

        Uses Playwright's accessibility API to get a semantic tree of
        the page.  Each interactive element gets a ref like ``e3`` that
        can be used in click/fill/type actions.

        This is the same approach OpenClaw uses -- far more reliable
        than CSS selectors because refs are semantic (based on ARIA
        roles), not structural (based on DOM).
        """
        url = page.url
        title = page.title() or ""

        elements: list[SnapshotElement] = []
        ref_counter = 0
        ref_map: dict[str, dict[str, Any]] = {}

        try:
            snapshot_tree = page.accessibility.snapshot()
            if snapshot_tree:
                self._walk_a11y_tree(
                    snapshot_tree, elements, ref_map, [0],
                    max_elements=100,
                )
        except Exception as e:
            logger.warning("Accessibility snapshot failed, falling back to DOM query: %s", e)
            self._fallback_dom_snapshot(page, elements, ref_map)

        # Store ref map for resolving clicks/fills
        self._snapshot_refs = ref_map

        # Get a text summary of the page content (non-interactive)
        text_summary = ""
        try:
            text_summary = self._extract_readable_text(page)
            if len(text_summary) > 2000:
                text_summary = text_summary[:2000] + "..."
        except Exception:
            pass

        return PageSnapshot(
            url=url,
            title=title,
            elements=elements,
            text_summary=text_summary,
        )

    def _walk_a11y_tree(
        self,
        node: dict[str, Any],
        elements: list[SnapshotElement],
        ref_map: dict[str, dict[str, Any]],
        counter: list[int],
        max_elements: int = 100,
        depth: int = 0,
    ) -> None:
        """Recursively walk the accessibility tree and extract interactive elements."""
        if len(elements) >= max_elements:
            return

        role = node.get("role", "")
        name = node.get("name", "")
        value = node.get("value", "")

        # Roles that represent interactive elements the agent can act on
        interactive_roles = {
            "button", "link", "textbox", "checkbox", "radio",
            "combobox", "listbox", "option", "menuitem", "menu",
            "tab", "switch", "slider", "spinbutton", "searchbox",
            "menuitemcheckbox", "menuitemradio", "treeitem",
        }

        if role in interactive_roles and (name or value):
            ref_id = f"e{counter[0]}"
            counter[0] += 1

            elements.append(SnapshotElement(
                ref=ref_id,
                role=role,
                name=name,
                value=str(value) if value else "",
                disabled=node.get("disabled", False),
                focused=node.get("focused", False),
            ))

            # Store the node info for resolving later
            ref_map[ref_id] = {
                "role": role,
                "name": name,
                "value": value,
            }

        # Recurse into children
        children = node.get("children", [])
        for child in children:
            if len(elements) >= max_elements:
                break
            self._walk_a11y_tree(child, elements, ref_map, counter, max_elements, depth + 1)

    def _fallback_dom_snapshot(
        self,
        page: Any,
        elements: list[SnapshotElement],
        ref_map: dict[str, dict[str, Any]],
    ) -> None:
        """Fallback: query interactive DOM elements directly when a11y tree fails."""
        try:
            interactive_data = page.evaluate("""() => {
                const selectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';
                const els = document.querySelectorAll(selectors);
                const results = [];
                for (let i = 0; i < Math.min(els.length, 100); i++) {
                    const el = els[i];
                    const tag = el.tagName.toLowerCase();
                    const type = el.type || '';
                    const role = el.getAttribute('role') || tag;
                    const name = el.getAttribute('aria-label')
                        || el.textContent?.trim().substring(0, 80)
                        || el.getAttribute('placeholder')
                        || el.getAttribute('title')
                        || '';
                    const value = el.value || '';
                    if (!name && !value) continue;
                    results.push({
                        role: role === 'a' ? 'link' : role === 'input' ? (type || 'textbox') : role,
                        name: name,
                        value: value,
                        disabled: el.disabled || false,
                        selector: buildSelector(el),
                    });
                }
                function buildSelector(el) {
                    if (el.id) return '#' + CSS.escape(el.id);
                    const tag = el.tagName.toLowerCase();
                    const nth = Array.from(el.parentNode?.children || [])
                        .filter(c => c.tagName === el.tagName)
                        .indexOf(el);
                    const parent = el.parentNode?.id ? '#' + CSS.escape(el.parentNode.id) : '';
                    return parent + ' > ' + tag + ':nth-of-type(' + (nth + 1) + ')';
                }
                return results;
            }""")

            for i, item in enumerate(interactive_data or []):
                ref_id = f"e{i}"
                elements.append(SnapshotElement(
                    ref=ref_id,
                    role=item.get("role", "unknown"),
                    name=item.get("name", ""),
                    value=item.get("value", ""),
                    disabled=item.get("disabled", False),
                ))
                ref_map[ref_id] = {
                    "role": item.get("role", ""),
                    "name": item.get("name", ""),
                    "selector": item.get("selector", ""),
                    "fallback": True,
                }
        except Exception as e:
            logger.error("DOM fallback snapshot also failed: %s", e)

    def _resolve_ref(self, page: Any, ref: str) -> Any | None:
        """Resolve a snapshot ref (e.g., 'e3') to a Playwright locator.

        Uses the stored ref map from the last snapshot to find the element.
        Strategy:
        1. If fallback mode (CSS selector stored), use that directly
        2. Otherwise, use getByRole() with the stored role + name
        """
        ref_info = self._snapshot_refs.get(ref)
        if ref_info is None:
            return None

        try:
            # Fallback refs have a CSS selector
            if ref_info.get("fallback") and ref_info.get("selector"):
                locator = page.locator(ref_info["selector"])
                if locator.count() > 0:
                    return locator.first
                return None

            # Primary path: use getByRole with name
            role = ref_info.get("role", "")
            name = ref_info.get("name", "")

            if role and name:
                locator = page.get_by_role(role, name=name)
                count = locator.count()
                if count == 1:
                    return locator
                elif count > 1:
                    # Multiple matches: return the first visible one
                    return locator.first
                # Name didn't match exactly, try contains
                locator = page.get_by_role(role, name=name, exact=False)
                if locator.count() > 0:
                    return locator.first

            # Last resort: try matching by text content
            if name:
                locator = page.get_by_text(name, exact=False)
                if locator.count() > 0:
                    return locator.first

        except Exception as e:
            logger.warning("Failed to resolve ref '%s': %s", ref, e)

        return None

    # ===================================================================
    # Content Extraction
    # ===================================================================

    def _extract_readable_text(self, page: Any) -> str:
        """Extract clean, readable text from a rendered page.

        First removes navigation/sidebar/cookie elements from the DOM,
        then uses a priority selector system to find the main content:
        1. Site-specific selectors (Wikipedia, MDN, etc.)
        2. Semantic HTML: <article>, <main>, [role="main"]
        3. Common CMS content classes
        4. Fallback: <body>

        Then cleans up the text: removes excessive whitespace,
        collapses blank lines, etc.
        """
        # Step 1: Remove noisy DOM elements before extraction
        try:
            page.evaluate("""() => {
                const selectors = [
                    'nav', 'footer', 'header', 'aside',
                    '[role="navigation"]', '[role="banner"]',
                    '.sidebar', '.nav', '.menu', '.toc',
                    '.cookie-banner', '.cookie-consent',
                    '.ad', '.ads', '.advertisement',
                    '#sidebar', '#nav', '#footer',
                    '.interlanguage-link', '.vector-menu',
                    '#p-lang', '#p-navigation', '#p-tb',
                    '.mw-indicators', '.mw-editsection',
                    '.navbox', '.catlinks', '.reference',
                    '.noprint',
                ];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                }
            }""")
        except Exception:
            pass  # Non-critical; proceed with extraction

        # Step 2: Priority selectors for main content
        content_selectors = [
            # Wikipedia-specific
            ".mw-parser-output",
            "#mw-content-text",
            # MDN-specific
            ".main-page-content",
            # Common semantic HTML
            "article",
            "main",
            "[role='main']",
            "#content",
            "#main-content",
            # CMS patterns
            ".post-content",
            ".article-content",
            ".entry-content",
            ".page-content",
            ".prose",
        ]

        text = ""
        for selector in content_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    raw = el.inner_text()
                    if raw and len(raw.strip()) > 100:
                        text = raw.strip()
                        break
            except Exception:
                continue

        # Fallback: full body text
        if not text:
            try:
                body = page.query_selector("body")
                if body:
                    text = body.inner_text().strip()
            except Exception:
                pass

        # Clean up
        return self._clean_text(text)

    def _extract_links(self, page: Any) -> list[dict[str, str]]:
        """Extract meaningful links from the current page.

        Filters out navigation, social, and utility links.
        Returns links with text and URL.
        """
        links: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        try:
            anchors = page.query_selector_all("a[href]")
            for a in anchors:
                try:
                    href = a.get_attribute("href") or ""
                    text = a.inner_text().strip()

                    # Skip empty, anchor-only, or javascript links
                    if (
                        not href
                        or href.startswith("#")
                        or href.startswith("javascript:")
                        or not text
                        or len(text) < 3
                        or len(text) > 200
                    ):
                        continue

                    # Resolve relative URLs
                    if href.startswith("/"):
                        base_url = page.url
                        parsed = urllib.parse.urlparse(base_url)
                        href = f"{parsed.scheme}://{parsed.netloc}{href}"
                    elif not href.startswith("http"):
                        continue

                    # Skip duplicates
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    # Skip social/utility links
                    skip_patterns = [
                        "facebook.com", "twitter.com", "instagram.com",
                        "linkedin.com", "youtube.com", "pinterest.com",
                        "login", "signup", "signin", "register",
                        "cookie", "privacy", "terms", "contact",
                        "/cdn-cgi/", "/wp-admin/", "/feed",
                    ]
                    if any(p in href.lower() for p in skip_patterns):
                        continue

                    links.append({"url": href, "text": text})

                    if len(links) >= self.config.max_links_returned * 2:
                        break  # Enough candidates
                except Exception:
                    continue
        except Exception:
            pass

        return links[:self.config.max_links_returned]

    # ===================================================================
    # Helpers
    # ===================================================================

    @staticmethod
    def _resolve_ddg_url(href: str) -> str:
        """Resolve DuckDuckGo redirect URLs to their real targets."""
        if not href:
            return ""

        # Handle //duckduckgo.com/l/?uddg=... redirects
        if "duckduckgo.com/l/" in href:
            if href.startswith("//"):
                href = "https:" + href
            try:
                parsed = urllib.parse.urlparse(href)
                params = urllib.parse.parse_qs(parsed.query)
                if "uddg" in params:
                    return urllib.parse.unquote(params["uddg"][0])
            except Exception:
                pass

        # Handle relative DDG URLs
        if href.startswith("//"):
            return "https:" + href

        return href

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean extracted text: collapse whitespace, remove junk lines."""
        if not text:
            return ""

        lines = text.split("\n")
        cleaned = []
        prev_empty = False

        for line in lines:
            line = line.strip()

            # Skip very short lines that are likely navigation remnants
            if len(line) <= 2 and not line.isdigit():
                if not prev_empty:
                    cleaned.append("")
                    prev_empty = True
                continue

            # Skip cookie/consent/nav patterns
            skip_patterns = [
                "accept cookies", "cookie policy", "privacy policy",
                "sign up", "log in", "subscribe", "newsletter",
                "advertisement", "sponsored",
            ]
            if any(p in line.lower() for p in skip_patterns):
                continue

            cleaned.append(line)
            prev_empty = not line

        result = "\n".join(cleaned).strip()

        # Collapse multiple blank lines into one
        result = re.sub(r"\n{3,}", "\n\n", result)

        return result

    @staticmethod
    def _is_content_link(url: str, text: str, query: str) -> bool:
        """Heuristic: is this link likely to lead to content-rich page?"""
        if not url or not text:
            return False

        # Must be an article-length link text
        if len(text) < 10:
            return False

        # Prefer links with query terms in them
        query_terms = set(query.lower().split())
        link_terms = set(text.lower().split())
        overlap = query_terms & link_terms
        if overlap:
            return True

        # Prefer wiki, article, post URLs
        content_indicators = [
            "/wiki/", "/article/", "/post/", "/blog/",
            "/news/", "/story/", "/research/",
        ]
        if any(ind in url.lower() for ind in content_indicators):
            return True

        return False

    # ===================================================================
    # Fallback page reading (when Playwright is unavailable)
    # ===================================================================

    def _fallback_read_page(self, url: str) -> PageContent:
        """Fallback page reading using urllib + HTML parser."""
        logger.info("BrowserEngine: using urllib fallback for read_page")
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.config.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "xhtml" not in content_type:
                    return PageContent(url=url, text=f"Not an HTML page: {content_type}")

                raw = resp.read(512 * 1024)
                html = raw.decode("utf-8", errors="replace")

            parser = _ReadableTextParser()
            parser.feed(html)
            text = parser.get_text()

            if not text:
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()

            return PageContent(
                url=url,
                title="",
                text=text[:self.config.max_content_chars],
            )
        except Exception as e:
            return PageContent(url=url, text=f"[ERROR] {e}")
