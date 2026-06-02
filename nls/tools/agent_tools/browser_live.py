"""Live browser tool — full interaction with the in-app Electron webview.

Supports: navigate, click, type, fill, scroll, screenshot, get_text,
get_interactive_elements.  All commands are routed over WebSocket to
the Angular frontend which executes them via webview.executeJavaScript().

When no UI is connected, falls back to headless Playwright on the local
machine (navigate + screenshot only).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Awaitable

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_ACTIONS = {
    "navigate",
    "click",
    "type",
    "fill",
    "scroll",
    "screenshot",
    "get_text",
    "get_interactive_elements",
}


class LiveBrowserTool:
    """Full-interaction browser tool via Electron webview or Playwright."""

    def __init__(
        self,
        emit_and_wait: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        playwright_fallback: Any | None = None,
        timeout: float = 60.0,
        workspace: str = "",
    ) -> None:
        self._emit_and_wait = emit_and_wait
        self._playwright = playwright_fallback
        self._timeout = timeout
        self._workspace = workspace
        self._visual_cortex: Any = None

    def set_visual_cortex(self, vc: Any) -> None:
        """Wire this tool into the VisualCortex so it can capture the Electron webview."""
        self._visual_cortex = vc
        vc.set_browser_engine(self)

    async def _async_capture_frame(self) -> Any | None:
        """Capture the Electron webview as a PIL Image via WebSocket screenshot_raw.

        Sends a screenshot_raw browser_command to the Angular frontend, which calls
        webview.capturePage() and returns the image as base64 JPEG.
        Returns a PIL Image, or None if the webview is unavailable.
        """
        if self._emit_and_wait is None:
            return None
        try:
            import base64 as _b64
            import io as _io

            response = await asyncio.wait_for(
                self._emit_and_wait({
                    "type": "browser_command",
                    "request_id": str(uuid.uuid4()),
                    "action": "screenshot_raw",
                }),
                timeout=10.0,
            )
            b64 = response.get("image_base64", "")
            if not b64:
                return None
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            data = _b64.b64decode(b64)
            from PIL import Image
            return Image.open(_io.BytesIO(data)).convert("RGB")
        except Exception as exc:
            logger.debug("LiveBrowserTool._async_capture_frame failed: %s", exc)
            return None

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Interact with the agent's built-in browser. The user can see "
            "the page live in the app.\n\n"
            "ACTIONS:\n"
            "- navigate: Go to a URL. Returns page info AND a list of "
            "interactive elements with their CSS selectors. Params: url\n"
            "- click: Click an element. Params: selector (use exact CSS "
            "selectors from navigate/get_interactive_elements output)\n"
            "- type: Type text into the focused element (appends). "
            "Use {ENTER} for Enter, {TAB} for Tab, {ESCAPE} for Escape, "
            "{BACKSPACE} for Backspace. Params: text\n"
            "- fill: Clear an input and set its value. Params: selector, value\n"
            "- scroll: Scroll the page. Params: direction (up/down), amount (px, default 500)\n"
            "- screenshot: Capture the current page as text description.\n"
            "- get_text: Get all visible text on the page.\n"
            "- get_interactive_elements: List clickable/typeable elements "
            "with their CSS selectors.\n\n"
            "IMPORTANT: navigate already returns interactive elements. "
            "Use the EXACT selectors from the output — never guess. "
            "After navigate, call fill/click with those selectors."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "The browser action to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for 'navigate' action).",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the target element (for click/fill).",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action).",
                },
                "value": {
                    "type": "string",
                    "description": "Value to fill into an input (for 'fill' action).",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (for 'scroll' action). Default: down.",
                },
                "amount": {
                    "type": "number",
                    "description": "Scroll amount in pixels (for 'scroll' action). Default: 500.",
                },
            },
            "required": ["action"],
        }

    def _resolve_url(self, url: str) -> str:
        """Resolve a URL, handling file://, absolute paths, and relative paths."""
        if url.startswith(("http://", "https://", "file://", "chrome", "data:")):
            return url

        candidate = Path(url)
        if candidate.is_absolute():
            if candidate.exists():
                return candidate.as_uri()
            return candidate.as_uri()

        if self._workspace:
            ws_candidate = Path(self._workspace) / url
            if ws_candidate.exists():
                return ws_candidate.as_uri()

        return "https://" + url

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "").strip()
        if action not in _ACTIONS:
            return ToolResult(
                content=f"Error: unknown action '{action}'. "
                f"Valid actions: {', '.join(sorted(_ACTIONS))}",
                is_error=True,
            )

        # Navigate needs URL validation + local path resolution
        if action == "navigate":
            url = params.get("url", "").strip()
            if not url:
                return ToolResult(content="Error: 'url' is required for navigate.", is_error=True)
            url = self._resolve_url(url)
            params = {**params, "url": url}

        if self._emit_and_wait is not None:
            return await self._send_command(action, params, signal)

        # Playwright fallback (navigate + screenshot only)
        if self._playwright is not None and action == "navigate":
            return await self._execute_playwright(params.get("url", ""), signal)

        if self._playwright is None and self._emit_and_wait is None:
            return ToolResult(
                content="Error: No browser backend available.",
                is_error=True,
            )

        return ToolResult(
            content=f"Error: action '{action}' is not available in headless fallback mode.",
            is_error=True,
        )

    async def _send_command(
        self, action: str, params: dict[str, Any], signal: asyncio.Event | None
    ) -> ToolResult:
        """Send any action to the Electron webview via WS and await response."""
        request_id = str(uuid.uuid4())
        command: dict[str, Any] = {
            "type": "browser_command",
            "request_id": request_id,
            "action": action,
        }
        # Forward relevant params
        for key in ("url", "selector", "text", "value", "direction", "amount"):
            if key in params and params[key] is not None:
                command[key] = params[key]

        try:
            assert self._emit_and_wait is not None
            response = await asyncio.wait_for(
                self._emit_and_wait(command),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                content=f"Browser '{action}' timed out after {self._timeout}s",
                is_error=True,
            )
        except Exception as e:
            logger.error("Browser command '%s' failed: %s", action, e)
            return ToolResult(content=f"Error: {e}", is_error=True)

        status = response.get("status", "error")
        if status == "error":
            return ToolResult(
                content=f"Browser error: {response.get('error', 'Unknown error')}",
                is_error=True,
            )

        # Challenge detected (CAPTCHA, bot check, etc.)
        is_challenge = status == "challenge"

        result_text = response.get("result", f"Action '{action}' completed.")

        # After a successful navigate, auto-discover interactive elements
        # so the model has real CSS selectors instead of guessing.
        if action == "navigate" and not is_challenge:
            try:
                elements_result = await self._send_command(
                    "get_interactive_elements", {}, signal,
                )
                if not elements_result.is_error and elements_result.content:
                    result_text += (
                        "\n\n--- Interactive elements on this page ---\n"
                        + elements_result.content
                    )
            except Exception as e:
                logger.warning("Auto get_interactive_elements failed: %s", e)

        return ToolResult(
            content=result_text,
            is_error=is_challenge,
            details={**response, "request_id": request_id},
        )

    async def _execute_playwright(self, url: str, signal: asyncio.Event | None) -> ToolResult:
        """Fallback: navigate via headless Playwright."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, self._playwright.navigate, url,
            )
            return ToolResult(
                content=result.text,
                is_error=not result.success,
                details={"url": url, "backend": "playwright"},
            )
        except Exception as e:
            logger.error("Playwright navigation failed: %s", e)
            return ToolResult(
                content=f"Error: Headless browser navigation failed: {e}",
                is_error=True,
            )


def create_live_browser_tool(
    emit_and_wait: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    playwright_fallback: Any | None = None,
    timeout: float = 60.0,
    workspace: str = "",
) -> LiveBrowserTool:
    """Factory: create a live browser tool."""
    return LiveBrowserTool(
        emit_and_wait=emit_and_wait,
        playwright_fallback=playwright_fallback,
        timeout=timeout,
        workspace=workspace,
    )
