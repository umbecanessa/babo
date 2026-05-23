"""Vision tool — image understanding via the runtime vision endpoint.

Gives the agent the ability to understand images, screenshots, and
documents by sending them to a configured vision service on the runtime.

Supports:
  - Describing an image (general description + OCR)
  - Asking a specific question about an image
  - Analyzing screenshots from the browser tool
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class VisionTool:
    """Vision tool that calls the runtime vision HTTP endpoint."""

    def __init__(self, runtime_url: str) -> None:
        self._base_url = runtime_url.rstrip("/")

    @property
    def name(self) -> str:
        return "vision"

    @property
    def description(self) -> str:
        return (
            "Analyze images, screenshots, and documents using AI vision. "
            "Actions:\n"
            "- describe: Get a detailed description + OCR text extraction.\n"
            "- ask: Ask a specific question about an image.\n\n"
            "Provide the image as a local file path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["describe", "ask"],
                    "description": "Vision action to perform",
                },
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file to analyze",
                },
                "question": {
                    "type": "string",
                    "description": "Question to ask about the image (for 'ask' action)",
                },
            },
            "required": ["action", "image_path"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "").strip()
        image_path = params.get("image_path", "").strip()

        if not action:
            return ToolResult(content="Error: 'action' is required.", is_error=True)
        if not image_path:
            return ToolResult(content="Error: 'image_path' is required.", is_error=True)

        path = Path(image_path)
        if not path.exists():
            return ToolResult(
                content=f"Error: Image file not found: {image_path}",
                is_error=True,
            )

        try:
            image_bytes = path.read_bytes()
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
        except Exception as e:
            return ToolResult(content=f"Error reading image: {e}", is_error=True)

        try:
            import httpx
        except ImportError:
            try:
                import aiohttp
                return await self._execute_aiohttp(action, params, image_b64)
            except ImportError:
                return ToolResult(
                    content="Error: httpx or aiohttp required for vision tool",
                    is_error=True,
                )

        try:
            if action == "describe":
                return await self._describe(image_b64)
            elif action == "ask":
                question = params.get("question", "Describe this image.")
                return await self._ask(image_b64, question)
            else:
                return ToolResult(
                    content=f"Error: Unknown action '{action}'. Use 'describe' or 'ask'.",
                    is_error=True,
                )
        except Exception as e:
            logger.error("VisionTool.%s failed: %s", action, e)
            return ToolResult(
                content=f"Error: Vision action '{action}' failed: {e}",
                is_error=True,
            )

    async def _describe(self, image_b64: str) -> ToolResult:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/vision/describe",
                data={"image_base64": image_b64},
            )
            resp.raise_for_status()
            data = resp.json()

        parts = [f"Description: {data['description']}"]
        if data.get("ocr_text"):
            parts.append(f"\nText found in image:\n{data['ocr_text']}")
        parts.append(f"\n(processed in {data.get('processing_time', '?')}s)")

        return ToolResult(content="\n".join(parts))

    async def _ask(self, image_b64: str, question: str) -> ToolResult:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/vision/ask",
                data={"image_base64": image_b64, "question": question},
            )
            resp.raise_for_status()
            data = resp.json()

        return ToolResult(
            content=f"Answer: {data['answer']}\n"
            f"(processed in {data.get('processing_time', '?')}s)",
        )

    async def _execute_aiohttp(
        self, action: str, params: dict, image_b64: str,
    ) -> ToolResult:
        """Fallback using aiohttp when httpx is not available."""
        import aiohttp

        url = f"{self._base_url}/vision/{'describe' if action == 'describe' else 'ask'}"
        form_data = {"image_base64": image_b64}
        if action == "ask":
            form_data["question"] = params.get("question", "Describe this image.")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form_data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if action == "describe":
            parts = [f"Description: {data['description']}"]
            if data.get("ocr_text"):
                parts.append(f"\nText found in image:\n{data['ocr_text']}")
            return ToolResult(content="\n".join(parts))

        return ToolResult(content=f"Answer: {data['answer']}")


def create_vision_tool(runtime_url: str) -> VisionTool:
    """Factory: create a vision tool connected to the runtime."""
    return VisionTool(runtime_url=runtime_url)
