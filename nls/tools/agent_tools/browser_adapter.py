"""Browser adapter -- AI agent browser tool powered by browser-use.

Wraps browser-use's Browser (Playwright + stealth + anti-detection) into
the v2 AgentTool protocol.  Provides interactive browser automation with
actions: navigate, click, fill, type, snapshot, screenshot, evaluate,
press, select_option, wait_for, hover, scroll, close, authenticate.

Elements are tracked via ``data-nls-ref`` attributes injected during
snapshots.  The agent references elements by their integer index from the
most recent snapshot (e.g. ``ref=5``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_ALL_ACTIONS = [
    "navigate", "click", "fill", "type", "snapshot", "screenshot",
    "evaluate", "press", "select_option", "wait_for", "hover",
    "scroll", "close", "authenticate",
]

_INTERACTIVE_SELECTOR = (
    'a[href], button, input:not([type="hidden"]), select, textarea, '
    '[role="button"], [role="link"], [role="tab"], [role="menuitem"], '
    '[role="checkbox"], [role="radio"], [role="switch"], '
    '[contenteditable="true"], summary, [onclick]'
)

_SNAPSHOT_JS = """() => {
    document.querySelectorAll('[data-nls-ref]')
        .forEach(e => e.removeAttribute('data-nls-ref'));

    const SEL = '%s';
    const all = document.querySelectorAll(SEL);
    const elements = [];
    let idx = 0;

    all.forEach(el => {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0 &&
            el.tagName !== 'INPUT' && el.tagName !== 'SELECT') return;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return;

        el.setAttribute('data-nls-ref', String(idx));

        const tag = el.tagName.toLowerCase();
        const text = (el.innerText || el.textContent || '').trim()
            .replace(/\\s+/g, ' ').substring(0, 60);
        const role = el.getAttribute('role') || '';
        const type = el.getAttribute('type') || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const name = ariaLabel || text;
        const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
        const value = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')
            ? (el.value || '').substring(0, 40) : '';
        const checked = el.checked;
        const disabled = el.disabled;

        let desc = tag;
        if (role) desc += '[' + role + ']';
        if (type) desc += '[type=' + type + ']';
        if (disabled) desc += '[disabled]';
        if (name) desc += ' "' + name + '"';
        if (placeholder && !name) desc += ' (' + placeholder + ')';
        if (href) desc += ' -> ' + href.substring(0, 80);
        if (value) desc += ' value="' + value + '"';
        if (checked !== undefined && checked) desc += ' [checked]';

        elements.push({i: idx, d: desc});
        idx++;
    });

    return {
        url: location.href,
        title: document.title,
        count: elements.length,
        elements: elements.slice(0, 200)
    };
}""" % _INTERACTIVE_SELECTOR.replace("'", "\\'")


_CDP_SKIP_URLS = (
    "localhost:4200", "localhost:9222",
    "devtools://", "chrome://", "chrome-extension://",
    "about:devtools",
)


class BrowserAdapterTool:
    """Browser tool powered by browser-use (Playwright + stealth).

    Lazy-initialises on first use.  The browser persists across calls
    so cookies and login sessions are preserved.

    When ``cdp_url`` is set, connects to an existing browser (the
    Electron app's embedded webview) via Chrome DevTools Protocol
    instead of launching a standalone Chromium window.
    """

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: str = "",
        on_navigation: Any | None = None,
        workspace_path: str = "",
        cdp_url: str = "",
        request_auth: Any | None = None,
    ) -> None:
        self._headless = headless
        self._user_data_dir = user_data_dir or None
        self._on_navigation = on_navigation
        self._workspace_path = workspace_path
        self._cdp_url = cdp_url
        self._cdp_mode = False
        self._browser: Any | None = None
        self._page: Any | None = None
        self._started = False
        self._visual_cortex: Any | None = None
        self._request_auth = request_auth
        self._auth_session: Any | None = None
        self._auth_poll_task: Any | None = None
        self._auth_cookies: list[dict] = []
        self._copilot_queue: Any | None = None
        self._emit_set_cookies: Any | None = None

    @staticmethod
    def _ensure_browseruse_config_dir() -> None:
        """Set BROWSER_USE_CONFIG_DIR before importing browser-use.

        browser-use tries to mkdir ~/.config/browseruse on import which
        may fail on macOS where ~/.config is owned by root.  Redirect it
        to the NLS data directory (or a temp fallback).
        """
        import os
        if os.environ.get("BROWSER_USE_CONFIG_DIR"):
            return
        data_dir = os.environ.get("NLS_DATA_DIR", "")
        if data_dir:
            bu_cfg = os.path.join(data_dir, "browseruse")
            os.makedirs(bu_cfg, exist_ok=True)
            os.environ["BROWSER_USE_CONFIG_DIR"] = bu_cfg
        else:
            import tempfile
            fallback = os.path.join(tempfile.gettempdir(), "nls-browseruse")
            os.makedirs(fallback, exist_ok=True)
            os.environ["BROWSER_USE_CONFIG_DIR"] = fallback

    async def _ensure_page(self) -> Any:
        """Lazily start browser-use and return the active Page."""
        if self._page is not None:
            try:
                await self._page.get_url()
                return self._page
            except Exception:
                self._page = None
                if self._cdp_mode:
                    self._started = False

        if not self._started:
            self._ensure_browseruse_config_dir()

            try:
                from browser_use import Browser as BrowserUse
            except ImportError:
                raise RuntimeError(
                    "browser-use is not installed. "
                    "Run: pip install browser-use"
                )

            if self._cdp_url:
                await self._start_cdp(BrowserUse)
            else:
                await self._start_standalone(BrowserUse)

            if self._visual_cortex is not None:
                self._visual_cortex.set_browser_engine(self)
                logger.debug("BrowserAdapterTool: wired visual cortex")

        if self._cdp_mode:
            self._page = await self._acquire_cdp_page()
        else:
            self._page = await self._browser.get_current_page()
            if self._page is None:
                self._page = await self._browser.new_page()

        if self._auth_cookies and self._cdp_mode and self._page:
            logger.info(
                "BrowserAdapterTool: injecting %d pending auth cookies "
                "into newly connected CDP page",
                len(self._auth_cookies),
            )
            await self._inject_cookies_cdp(self._auth_cookies)
            self._auth_cookies = []

        return self._page

    async def _acquire_cdp_page(self) -> Any:
        """In CDP mode, find and attach to the Electron <webview> target.

        Electron does not support Target.createTarget so we cannot call
        new_page().  Instead we:
        1. Query /json/list on the CDP port to enumerate targets.
        2. Find the one with type "webview" (the <webview> tag from
           agent-browser component).
        3. Attach a CDP session to it (browser-use's SessionManager will
           handle the attachedToTarget event and create the session).
        4. Set it as the focused target and return a Page object.

        Falls back to whatever get_current_page() returns if no webview
        is found (e.g. standalone Chromium with CDP).
        """
        from browser_use.actor.page import Page as PageActor

        webview_id = await self._find_webview_target_id()

        if webview_id:
            sm = self._browser.session_manager
            if sm and not sm._get_session_for_target(webview_id):
                try:
                    cdp = self._browser._cdp_client_root
                    await cdp.send.Target.attachToTarget(
                        params={"targetId": webview_id, "flatten": True},
                    )
                    for _ in range(30):
                        if sm._get_session_for_target(webview_id):
                            break
                        await asyncio.sleep(0.1)
                except Exception as exc:
                    logger.warning(
                        "BrowserAdapterTool: failed to attach webview target: %s",
                        exc,
                    )
                    webview_id = None

            if webview_id:
                self._browser.agent_focus_target_id = webview_id
                logger.info(
                    "BrowserAdapterTool: focused on <webview> target %s",
                    webview_id[:8],
                )
                return PageActor(self._browser, webview_id)

        page = await self._browser.get_current_page()
        if page is not None:
            try:
                url = await page.get_url()
            except Exception:
                url = ""
            if self._is_electron_internal(url):
                logger.warning(
                    "BrowserAdapterTool: only Electron renderer available "
                    "(%s), falling back to standalone browser",
                    url[:80],
                )
                return await self._fallback_to_standalone()
        return page

    async def _find_webview_target_id(self) -> str | None:
        """Query the CDP /json/list endpoint for the <webview> target."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._cdp_url}/json/list")
                targets = resp.json()
        except Exception as exc:
            logger.debug("BrowserAdapterTool: /json/list failed: %s", exc)
            return None

        for t in targets:
            if t.get("type") == "webview":
                return t["id"]
        for t in targets:
            url = t.get("url", "")
            if t.get("type") == "page" and not self._is_electron_internal(url):
                return t["id"]
        return None

    async def _fallback_to_standalone(self) -> Any:
        """Abandon CDP and start a standalone browser instead."""
        try:
            from browser_use import Browser as BrowserUse
        except ImportError:
            raise RuntimeError("browser-use is not installed.")
        await self._start_standalone(BrowserUse)
        page = await self._browser.get_current_page()
        if page is None:
            page = await self._browser.new_page()
        return page

    @staticmethod
    def _is_electron_internal(url: str) -> bool:
        """Return True if *url* belongs to the Electron shell, not the webview.

        IMPORTANT: Angular's HTML5 history router uses pushState so the CDP target
        URL changes from file:///path/index.html to file:///chat/agentId etc. after
        every in-app navigation.  The original index.html / renderer/ checks would
        therefore fail to recognise the main renderer after the first route change,
        causing the browser tool to accidentally attach to it.

        All file:// URLs are Electron-internal — legitimate browser content is always
        loaded via http/https, never via the local filesystem.
        """
        if not url or url == "about:blank":
            return False
        if any(skip in url for skip in _CDP_SKIP_URLS):
            return True
        # Every file:// URL is the Electron renderer (either the original index.html
        # load or a post-pushState path like file:///tasks/agentId).
        if url.startswith("file://"):
            return True
        return False

    async def _start_standalone(self, BrowserUse: Any) -> None:
        """Launch a standalone Chromium via browser-use."""
        kwargs: dict[str, Any] = {
            "headless": self._headless,
            "keep_alive": True,
        }
        if self._user_data_dir:
            kwargs["user_data_dir"] = self._user_data_dir
        self._browser = BrowserUse(**kwargs)
        await self._browser.start()
        self._started = True
        self._cdp_mode = False
        logger.info("BrowserAdapterTool: browser-use started (standalone)")

    @staticmethod
    def _patch_security_watchdog_for_cdp() -> None:
        """Allow file:// URLs in browser-use's SecurityWatchdog.

        Electron's renderer uses file:// URLs which have no hostname.
        browser-use rejects URLs without a hostname and closes the tab,
        which kills the Electron app.  We patch _is_url_allowed once to
        let file:// (and the Electron renderer) through.
        """
        try:
            from browser_use.browser.watchdogs.security_watchdog import (
                SecurityWatchdog,
            )

            if getattr(SecurityWatchdog, "_nls_cdp_patched", False):
                return

            _orig = SecurityWatchdog._is_url_allowed

            def _patched(self_sw: Any, url: str) -> bool:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    if parsed.scheme == "file":
                        return True
                except Exception:
                    pass
                return _orig(self_sw, url)

            SecurityWatchdog._is_url_allowed = _patched  # type: ignore[attr-defined]
            SecurityWatchdog._nls_cdp_patched = True  # type: ignore[attr-defined]
            logger.debug("Patched SecurityWatchdog to allow file:// URLs")
        except Exception as exc:
            logger.debug("Could not patch SecurityWatchdog: %s", exc)

    async def _start_cdp(self, BrowserUse: Any) -> None:
        """Connect to an existing browser via CDP (Electron webview)."""
        self._patch_security_watchdog_for_cdp()

        last_err: Exception | None = None
        for attempt in range(3):
            browser = None
            try:
                browser = BrowserUse(
                    cdp_url=self._cdp_url,
                    keep_alive=True,
                )
                await browser.start()
                self._browser = browser
                self._started = True
                self._cdp_mode = True
                logger.info(
                    "BrowserAdapterTool: connected via CDP to %s",
                    self._cdp_url,
                )
                return
            except Exception as exc:
                last_err = exc
                if browser is not None:
                    try:
                        await browser.stop()
                    except Exception:
                        pass
                logger.debug(
                    "BrowserAdapterTool: CDP attempt %d failed: %s",
                    attempt + 1, exc,
                )
                await asyncio.sleep(1.0)

        logger.warning(
            "BrowserAdapterTool: CDP connection failed after 3 attempts "
            "(%s) -- falling back to standalone browser",
            last_err,
        )
        await self._start_standalone(BrowserUse)

    async def _get_element(self, ref: str) -> Any:
        """Resolve a ref index to a browser-use Element."""
        page = await self._ensure_page()
        selector = f'[data-nls-ref="{ref}"]'
        elements = await page.get_elements_by_css_selector(selector)
        if not elements:
            return None
        return elements[0]

    async def _take_snapshot(self) -> str:
        """Build a text snapshot of all interactive elements on the page."""
        page = await self._ensure_page()
        try:
            raw = await page.evaluate(_SNAPSHOT_JS)
        except Exception as e:
            url = ""
            try:
                url = await page.get_url()
            except Exception:
                pass
            return f"Page: (error reading state: {e})\nURL: {url}"

        # browser-use evaluate() may return a JSON string or a dict
        if isinstance(raw, str):
            import json
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result = {"url": "", "title": "", "count": 0, "elements": []}
        else:
            result = raw if isinstance(raw, dict) else {}

        url = result.get("url", "")
        title = result.get("title", "")
        count = result.get("count", 0)
        elements = result.get("elements", [])

        lines = [f"Page: {title}", f"URL: {url}", ""]
        if elements:
            lines.append(f"{count} interactive elements:")
            for el in elements:
                lines.append(f"  [{el['i']}] {el['d']}")
            if count > 200:
                lines.append(f"  ... and {count - 200} more (scroll or narrow your search)")
        else:
            lines.append("(no interactive elements found)")

        return "\n".join(lines)

    def _resolve_url(self, url: str) -> str:
        """Resolve a URL, handling file://, absolute paths, and relative paths."""
        if url.startswith(("http://", "https://", "file://", "chrome", "data:")):
            return url

        import pathlib as _pl

        # Absolute path (Unix /... or Windows C:\...)
        candidate = _pl.Path(url)
        if candidate.is_absolute():
            if candidate.exists():
                return candidate.as_uri()
            return candidate.as_uri()

        # Relative path — resolve against workspace
        if self._workspace_path:
            ws_candidate = _pl.Path(self._workspace_path) / url
            if ws_candidate.exists():
                return ws_candidate.as_uri()

        return "https://" + url

    # -- AgentTool protocol -------------------------------------------------

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Interactive web browser (Chromium with stealth/anti-detection). "
            "The runtime may attach to the **in-app** embedded webview "
            "(user sees pages inside the app) or drive a **standalone** "
            "browser window — same tool and actions either way. "
            "Elements are identified by index numbers from the snapshot "
            "returned after each action (e.g. ref=5).\n\n"
            "Actions:\n"
            "- navigate: Load a URL.\n"
            "- click: Click element by ref index.\n"
            "- fill: Clear input and set value by ref.\n"
            "- type: Type text into element by ref (triggers autocomplete).\n"
            "- select_option: Select dropdown option by ref + value/label.\n"
            "- hover: Hover over element (reveals dropdowns/tooltips).\n"
            "- scroll: Scroll the page (direction: up/down).\n"
            "- wait_for: Wait for text to appear on page.\n"
            "- snapshot: Refresh the element list without navigating.\n"
            "- screenshot: Capture page as image.\n"
            "- press: Press keyboard key (e.g. Enter, Tab, Escape).\n"
            "- evaluate: Run JavaScript on the page.\n"
            "- close: End the browser session.\n"
            "- authenticate: Open a standalone browser window for the "
            "user to sign in. REQUIRED whenever a site requires login "
            "(Google, GitHub, Microsoft, etc.). Pass the login URL "
            "(e.g. url='https://accounts.google.com'). Returns "
            "immediately after opening the browser. A background "
            "process watches for sign-in and captures cookies "
            "automatically. NEXT you MUST call ask_user() to tell the "
            "user to sign in in the browser window and let you know "
            "when done. Once the user confirms, cookies are already "
            "captured — just proceed with navigation.\n\n"
            "AUTH FLOW (2 steps):\n"
            "  1. browser(action='authenticate', url='<login_url>')\n"
            "  2. ask_user('A sign-in browser has been opened. Please "
            "sign in there and let me know when you are done.')\n"
            "Then continue normally — cookies are injected "
            "automatically.\n\n"
            "IMPORTANT: If you encounter a sign-in page, an auth block, "
            "or 'This browser or app may not be secure', use authenticate "
            "immediately — do NOT try to log in inside the embedded "
            "browser.\n\n"
            "After every action you receive an updated snapshot with "
            "numbered elements. Use these numbers as ref for subsequent actions."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": _ALL_ACTIONS,
                    "description": "Browser action to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for 'navigate')",
                },
                "ref": {
                    "type": "string",
                    "description": "Element index from snapshot (e.g. '5') for click/fill/type/hover/select_option",
                },
                "value": {
                    "type": "string",
                    "description": "Text value for fill/type/select_option/wait_for",
                },
                "key": {
                    "type": "string",
                    "description": "Key name for press (e.g. 'Enter', 'Tab')",
                },
                "script": {
                    "type": "string",
                    "description": "JavaScript to evaluate",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down)",
                },
                "amount": {
                    "type": "integer",
                    "description": "Scroll amount in pixels (default: 500)",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "").strip()
        if not action:
            return ToolResult(content="Error: 'action' is required.", is_error=True)

        try:
            if action == "navigate":
                return await self._do_navigate(params)
            elif action == "click":
                return await self._do_click(params)
            elif action == "fill":
                return await self._do_fill(params)
            elif action == "type":
                return await self._do_type(params)
            elif action == "select_option":
                return await self._do_select(params)
            elif action == "hover":
                return await self._do_hover(params)
            elif action == "scroll":
                return await self._do_scroll(params)
            elif action == "wait_for":
                return await self._do_wait_for(params)
            elif action == "snapshot":
                snap = await self._take_snapshot()
                return ToolResult(content=snap)
            elif action == "screenshot":
                return await self._do_screenshot()
            elif action == "press":
                return await self._do_press(params)
            elif action == "evaluate":
                return await self._do_evaluate(params)
            elif action == "close":
                return await self._do_close()
            elif action == "authenticate":
                return await self._do_authenticate(params)
            else:
                return ToolResult(
                    content=f"Unknown action '{action}'. Available: {', '.join(_ALL_ACTIONS)}",
                    is_error=True,
                )
        except Exception as e:
            logger.error("BrowserAdapterTool.%s failed: %s", action, e, exc_info=True)
            return ToolResult(
                content=f"Browser '{action}' failed: {e}",
                is_error=True,
            )

    # -- Action implementations ---------------------------------------------

    async def _do_navigate(self, params: dict) -> ToolResult:
        url = params.get("url", "").strip()
        if not url:
            return ToolResult(content="Error: 'url' required.", is_error=True)

        url = self._resolve_url(url)

        page = await self._ensure_page()
        await page.goto(url)
        await asyncio.sleep(1.5)

        if self._on_navigation:
            try:
                import inspect
                title = await page.get_title()
                result = self._on_navigation("navigate", url, title)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

        snap = await self._take_snapshot()
        return ToolResult(content=snap)

    async def _do_click(self, params: dict) -> ToolResult:
        ref = params.get("ref", "").strip()
        if not ref:
            return ToolResult(content="Error: 'ref' required.", is_error=True)

        el = await self._get_element(ref)
        if el is None:
            return ToolResult(
                content=f"Element [{ref}] not found. Page may have changed -- take a snapshot first.",
                is_error=True,
            )
        await el.click()
        await asyncio.sleep(0.8)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Clicked [{ref}].\n\n{snap}")

    async def _do_fill(self, params: dict) -> ToolResult:
        ref = params.get("ref", "").strip()
        value = params.get("value", "")
        if not ref:
            return ToolResult(content="Error: 'ref' required.", is_error=True)

        el = await self._get_element(ref)
        if el is None:
            return ToolResult(
                content=f"Element [{ref}] not found.",
                is_error=True,
            )
        # Focus first so React/Angular's synthetic event system registers
        # the interaction — without this, programmatic fill bypasses the
        # framework's onChange handlers and the value is invisible to the app.
        await el.focus()
        await el.fill(value, clear=True)
        # Dispatch input + change events for frameworks (React, Angular, Vue)
        # that rely on them to update internal state rather than polling the DOM.
        try:
            page = await self._ensure_page()
            await page.evaluate(
                """(el) => {
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                el,
            )
        except Exception:
            pass
        await asyncio.sleep(0.5)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Filled [{ref}] with \"{value}\".\n\n{snap}")

    async def _do_type(self, params: dict) -> ToolResult:
        ref = params.get("ref", "").strip()
        value = params.get("value", "")
        if not ref:
            return ToolResult(content="Error: 'ref' required.", is_error=True)

        el = await self._get_element(ref)
        if el is None:
            return ToolResult(
                content=f"Element [{ref}] not found.",
                is_error=True,
            )
        await el.focus()
        await el.fill(value, clear=True)
        await asyncio.sleep(0.5)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Typed \"{value}\" into [{ref}].\n\n{snap}")

    async def _do_select(self, params: dict) -> ToolResult:
        ref = params.get("ref", "").strip()
        value = params.get("value", "")
        if not ref:
            return ToolResult(content="Error: 'ref' required.", is_error=True)

        el = await self._get_element(ref)
        if el is None:
            return ToolResult(
                content=f"Element [{ref}] not found.",
                is_error=True,
            )
        await el.select_option(value)
        await asyncio.sleep(0.3)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Selected \"{value}\" in [{ref}].\n\n{snap}")

    async def _do_hover(self, params: dict) -> ToolResult:
        ref = params.get("ref", "").strip()
        if not ref:
            return ToolResult(content="Error: 'ref' required.", is_error=True)

        el = await self._get_element(ref)
        if el is None:
            return ToolResult(
                content=f"Element [{ref}] not found.",
                is_error=True,
            )
        await el.hover()
        await asyncio.sleep(0.3)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Hovered [{ref}].\n\n{snap}")

    async def _do_scroll(self, params: dict) -> ToolResult:
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 500))
        page = await self._ensure_page()
        delta_y = amount if direction == "down" else -amount
        mouse = page.mouse
        await mouse.scroll(x=640, y=360, delta_y=delta_y)
        await asyncio.sleep(0.5)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Scrolled {direction} {amount}px.\n\n{snap}")

    async def _do_wait_for(self, params: dict) -> ToolResult:
        text = params.get("value", "").strip()
        if not text:
            return ToolResult(content="Error: 'value' (text to wait for) required.", is_error=True)

        page = await self._ensure_page()
        for _ in range(20):
            try:
                content = await page.evaluate(
                    "(t) => document.body.innerText.includes(t)", text
                )
                if content:
                    snap = await self._take_snapshot()
                    return ToolResult(content=f"Text \"{text}\" found.\n\n{snap}")
            except Exception:
                pass
            await asyncio.sleep(0.5)

        snap = await self._take_snapshot()
        return ToolResult(
            content=f"Text \"{text}\" not found after 10s.\n\n{snap}",
            is_error=True,
        )

    async def _do_screenshot(self) -> ToolResult:
        page = await self._ensure_page()
        try:
            b64 = await page.screenshot(format="jpeg", quality=60)
            return ToolResult(
                content=f"Screenshot captured ({len(b64)} bytes base64).",
                details={"screenshot_b64": b64},
            )
        except Exception as e:
            return ToolResult(content=f"Screenshot failed: {e}", is_error=True)

    async def _do_press(self, params: dict) -> ToolResult:
        key = params.get("key", "").strip()
        if not key:
            return ToolResult(content="Error: 'key' required.", is_error=True)

        page = await self._ensure_page()
        await page.press(key)
        _nav_keys = ("Enter", "Return", "Space")
        await asyncio.sleep(0.8 if key in _nav_keys else 0.3)
        snap = await self._take_snapshot()
        return ToolResult(content=f"Pressed {key}.\n\n{snap}")

    async def _do_evaluate(self, params: dict) -> ToolResult:
        script = params.get("script", "").strip()
        if not script:
            return ToolResult(content="Error: 'script' required.", is_error=True)

        page = await self._ensure_page()
        result = await page.evaluate(script)
        return ToolResult(content=f"JS result: {result}")

    def set_visual_cortex(self, visual_cortex: Any) -> None:
        """Register a VisualCortex so it gets wired when the browser starts."""
        self._visual_cortex = visual_cortex

    async def _async_capture_frame(self) -> Any | None:
        """Capture the active page as a PIL Image for the visual cortex.

        Must be awaited on the same event loop where the page was created
        (Playwright is loop-bound).  The VisualCortex calls this directly.
        """
        if self._page is None:
            return None
        try:
            png_bytes = await self._page.screenshot(type="jpeg", quality=60)
            from PIL import Image
            import io as _io
            return Image.open(_io.BytesIO(png_bytes)).convert("RGB")
        except Exception:
            return None

    _LOGIN_INDICATORS = (
        "accounts.google.com", "login.microsoftonline.com",
        "login.live.com", "github.com/login", "github.com/session",
        "appleid.apple.com/auth", "signin", "challenge",
        "/auth/login", "/login", "/oauth",
    )

    async def _do_authenticate(self, params: dict) -> ToolResult:
        """Open a standalone Chromium (with stealth) for the user to sign in.

        Returns immediately after opening the browser and starts a
        background task that polls for new cookies.  Once the user
        signs in (detected by cookie changes), the task automatically
        harvests cookies, injects them into the CDP webview, and
        closes the auth browser.
        """
        url = params.get("url", "").strip()
        if not url:
            return ToolResult(
                content="Error: 'url' required for authenticate action.",
                is_error=True,
            )
        url = self._resolve_url(url)

        self._ensure_browseruse_config_dir()

        try:
            from browser_use import Browser as BrowserUse
        except ImportError:
            return ToolResult(
                content="browser-use is not installed.",
                is_error=True,
            )

        logger.info("BrowserAdapterTool: opening auth browser for %s", url)

        auth_session = BrowserUse(headless=False)
        try:
            await auth_session.start()
            await auth_session.navigate_to(url)
        except Exception as exc:
            logger.error("BrowserAdapterTool: failed to open auth browser: %s", exc)
            try:
                await auth_session.stop()
            except Exception:
                pass
            return ToolResult(
                content=f"Could not open authentication browser: {exc}",
                is_error=True,
            )

        self._auth_session = auth_session
        self._auth_cookies = []

        self._auth_poll_task = asyncio.create_task(
            self._poll_auth_cookies(auth_session, url)
        )

        return ToolResult(
            content=(
                "A standalone sign-in browser has been opened at "
                f"{url}. The user can see it on their screen.\n"
                "NEXT STEP: Use ask_user() NOW to tell the user to "
                "complete sign-in in the browser window that just "
                "opened, and to let you know when they are done.\n"
                "Session cookies will be captured automatically once "
                "sign-in is detected."
            ),
        )

    async def _poll_auth_cookies(
        self, auth_session: Any, auth_url: str,
    ) -> None:
        """Background task: poll auth browser for sign-in completion.

        Waits for the page to settle, then watches for two signals:
        - URL navigates away from all login pages, OR
        - Cookie count jumps significantly (>=5 new cookies).
        Either indicates the user signed in.
        """
        _MIN_COOKIE_JUMP = 5

        # Grace period: let the page load and set its initial cookies
        await asyncio.sleep(10)

        baseline_count = 0
        try:
            baseline_count = len(await auth_session.cookies())
        except Exception:
            pass
        logger.info(
            "BrowserAdapterTool: auth poller baseline — %d cookies, "
            "watching for sign-in", baseline_count,
        )

        auth_url_lower = auth_url.lower()
        deadline = time.time() + 300  # 5 min from now
        try:
            while time.time() < deadline:
                await asyncio.sleep(3)

                # Check URL change
                try:
                    current_url = await auth_session.get_current_page_url()
                except Exception:
                    break
                cur_lower = current_url.lower()
                url_left_login = (
                    cur_lower != auth_url_lower
                    and not any(
                        ind in cur_lower
                        for ind in self._LOGIN_INDICATORS
                    )
                )

                # Check cookie jump
                try:
                    raw = await auth_session.cookies()
                except Exception:
                    break
                cookie_jump = len(raw) - baseline_count

                still_on_login = any(
                    ind in cur_lower for ind in self._LOGIN_INDICATORS
                )
                if url_left_login or (cookie_jump >= _MIN_COOKIE_JUMP and not still_on_login):
                    cookies = [
                        c if isinstance(c, dict) else c.dict()
                        for c in raw
                    ]
                    self._auth_cookies = cookies
                    logger.info(
                        "BrowserAdapterTool: auth sign-in detected — "
                        "%d cookies (+%d), url=%s",
                        len(cookies), cookie_jump,
                        current_url[:80],
                    )
                    if self._cdp_mode and self._page:
                        await self._inject_cookies_cdp(cookies)

                    if self._copilot_queue is not None:
                        try:
                            self._copilot_queue.put_nowait(
                                "Sign-in completed successfully. "
                                "The browser window has closed. "
                                "Session cookies are captured — proceed "
                                "with the next step."
                            )
                            logger.info(
                                "BrowserAdapterTool: auto-answered "
                                "ask_user via copilot_queue"
                            )
                        except Exception as eq:
                            logger.warning(
                                "BrowserAdapterTool: failed to auto-answer "
                                "ask_user: %s", eq,
                            )
                    break
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Auth cookie poller error: %s", exc)
        finally:
            try:
                await auth_session.stop()
            except Exception:
                pass
            if self._auth_session is auth_session:
                self._auth_session = None
            self._auth_poll_task = None

    async def _inject_cookies_cdp(self, cookies: list[dict]) -> None:
        """Inject cookies into the webview's partition.

        Strategy 1 (preferred): Send cookies to the Electron main process
        via WebSocket → IPC, which uses session.fromPartition() to write
        directly into the webview's persist:nls-agent cookie store.

        Strategy 2 (fallback): CDP Storage.setCookies on the webview
        target session.
        """
        try:
            # --- Strategy 1: Electron IPC via WebSocket ---
            if self._emit_set_cookies is not None:
                try:
                    result = await self._emit_set_cookies(cookies)
                    ok = result.get("ok", 0) if isinstance(result, dict) else 0
                    fail = result.get("fail", 0) if isinstance(result, dict) else 0
                    logger.info(
                        "Injected %d cookies via Electron IPC "
                        "(%d ok, %d fail)",
                        len(cookies), ok, fail,
                    )
                    if ok > 0:
                        return
                    logger.warning(
                        "Electron IPC cookie injection had 0 ok, "
                        "falling back to CDP",
                    )
                except Exception as exc:
                    logger.warning(
                        "Electron IPC cookie injection failed: %s, "
                        "falling back to CDP",
                        exc,
                    )

            # --- Strategy 2: CDP Storage.setCookies on webview session ---
            cdp_session = None
            focus_id = getattr(self._browser, "agent_focus_target_id", None)
            sm = getattr(self._browser, "session_manager", None)
            if focus_id and sm:
                cdp_session = sm._get_session_for_target(focus_id)

            if cdp_session is not None:
                cdp_client = cdp_session.cdp_client
                sid = cdp_session.session_id
                try:
                    await cdp_client.send.Storage.setCookies(
                        params={"cookies": cookies},
                        session_id=sid,
                    )
                    logger.info(
                        "Injected %d cookies via CDP Storage.setCookies "
                        "(session %s)",
                        len(cookies), str(sid)[:8],
                    )
                    return
                except Exception as exc:
                    logger.warning(
                        "CDP Storage.setCookies failed: %s", exc,
                    )

            # --- Strategy 3: root CDP ---
            cdp = (
                getattr(self._browser, "cdp_client", None)
                or getattr(self._browser, "_cdp_client_root", None)
            )
            if cdp is not None:
                try:
                    await cdp.send.Storage.setCookies(
                        params={"cookies": cookies},
                    )
                    logger.info(
                        "Injected %d cookies via root CDP",
                        len(cookies),
                    )
                except Exception as exc:
                    logger.warning("Root CDP cookie injection failed: %s", exc)
                return

            logger.warning("Cookie injection skipped — no method available")
        except Exception as exc:
            logger.warning("Cookie injection failed: %s", exc)

    async def _do_close(self) -> ToolResult:
        if self._browser is not None:
            if self._visual_cortex is not None:
                self._visual_cortex.set_browser_engine(None)
            if self._cdp_mode:
                try:
                    page = await self._browser.get_current_page()
                    if page is not None:
                        await page.goto("about:blank")
                except Exception:
                    pass
                self._page = None
            else:
                try:
                    await self._browser.stop()
                except Exception:
                    pass
                self._browser = None
                self._page = None
                self._started = False
        return ToolResult(content="Browser closed.")


def create_browser_tool(
    headless: bool = False,
    on_navigation: Any | None = None,
    user_data_dir: str = "",
    workspace_path: str = "",
    cdp_url: str = "",
    request_auth: Any | None = None,
    **_kwargs: Any,
) -> BrowserAdapterTool:
    """Factory: create a browser-use powered browser tool."""
    return BrowserAdapterTool(
        headless=headless,
        on_navigation=on_navigation,
        user_data_dir=user_data_dir,
        workspace_path=workspace_path,
        cdp_url=cdp_url,
        request_auth=request_auth,
    )
