"""Poller tool -- create, list, and remove HTTP polling jobs.

A poller is a scheduled HTTP request that runs at a fixed interval.
When new data arrives, it's fed back to the agent as a message.
Under the hood, pollers are scheduler jobs with ``action="http"``
plus a result handler that routes responses to the agent.

Skills also use this via ``SkillContext.register_poller()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

import httpx

from .base import AgentTool, ToolResult
from .scheduler import ScheduledJob, SchedulerManager

logger = logging.getLogger(__name__)


class PollerTool:
    """Agent tool for creating HTTP polling jobs.

    Each poller periodically hits a URL, and when the response changes
    or has content, it delivers the result back as an agent message.
    """

    def __init__(self, manager: SchedulerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "poller"

    @property
    def description(self) -> str:
        return (
            "Create, list, or remove HTTP polling jobs. A poller periodically "
            "fetches a URL and sends you the response. Use this to monitor "
            "APIs, check for updates, drain message queues, watch RSS feeds, "
            "or any periodic HTTP check."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "remove"],
                    "description": "Operation to perform",
                },
                "name": {
                    "type": "string",
                    "description": "Unique poller name (required for create/remove). Pass '-' for list.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to poll (required for create). Pass '-' for list/remove.",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST"],
                    "description": "HTTP method (default: GET)",
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers as key-value pairs",
                },
                "body": {
                    "type": "string",
                    "description": "Request body (for POST)",
                },
                "interval_seconds": {
                    "type": "number",
                    "description": "Seconds between polls (default: 60, minimum: 10)",
                },
                "on_data": {
                    "type": "string",
                    "description": (
                        "What to tell yourself when data arrives. "
                        "E.g. 'Summarize this weather data and notify me if rain is expected'. "
                        "The HTTP response is prepended to this message."
                    ),
                },
            },
            "required": ["action", "name"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        command = params.get("action") or params.get("command", "")

        if command == "list":
            return self._list_pollers()
        elif command == "remove":
            return self._remove_poller(params.get("name", ""))
        elif command == "create":
            return self._create_poller(params)
        else:
            return ToolResult(
                content=f"Unknown action: {command}. Use 'create', 'list', or 'remove'.",
                is_error=True,
            )

    def _list_pollers(self) -> ToolResult:
        pollers = {
            n: j for n, j in self._manager.jobs.items()
            if j.action == "http" or j.owner.startswith("poller:")
        }
        if not pollers:
            return ToolResult(content="No active pollers.")
        lines = []
        for name, job in pollers.items():
            status = "enabled" if job.enabled else "disabled"
            lines.append(
                f"  {name}: {job.action_url} every {job.interval_seconds}s "
                f"[{status}] (runs: {job.run_count})"
            )
        return ToolResult(content="Active pollers:\n" + "\n".join(lines))

    def _remove_poller(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(content="Error: 'name' is required", is_error=True)
        if self._manager.remove_job(name):
            return ToolResult(content=f"Poller '{name}' removed.")
        return ToolResult(content=f"Poller '{name}' not found.", is_error=True)

    def _create_poller(self, params: dict[str, Any]) -> ToolResult:
        name = params.get("name", "")
        url = params.get("url", "")
        if not name:
            return ToolResult(content="Error: 'name' is required", is_error=True)
        if not url:
            return ToolResult(content="Error: 'url' is required", is_error=True)

        interval = params.get("interval_seconds", 60)
        if interval < 10:
            return ToolResult(
                content="Error: interval_seconds must be >= 10",
                is_error=True,
            )

        on_data = params.get("on_data", "")
        method = params.get("method", "GET")
        headers = params.get("headers", {})
        body = params.get("body", "")

        job = ScheduledJob(
            name=name,
            schedule_type="interval",
            interval_seconds=interval,
            action="http",
            action_url=url,
            action_method=method,
            action_headers=headers if isinstance(headers, dict) else {},
            action_body=body,
            action_message=on_data,
            owner=f"poller:{name}",
            enabled=True,
        )

        self._manager.add_job(job)

        # Register a callback that does HTTP + routes response to agent
        async def _poll_and_deliver():
            await _execute_poll(
                job=job,
                on_agent_message=self._manager._on_agent_message,
            )

        self._manager.register_callback(name, _poll_and_deliver)
        # Override the action to callback so the scheduler uses our function
        job.action = "callback"
        self._manager._save()

        return ToolResult(
            content=f"Poller '{name}' created: {method} {url} every {interval}s."
        )


async def _execute_poll(
    job: ScheduledJob,
    on_agent_message: Callable[[str], Awaitable[Any]] | None,
) -> None:
    """Execute a poll: HTTP request + deliver result to agent."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if job.action_method.upper() == "POST":
                resp = await client.post(
                    job.action_url,
                    headers=job.action_headers,
                    content=job.action_body,
                )
            else:
                resp = await client.get(
                    job.action_url,
                    headers=job.action_headers,
                )

            response_text = resp.text[:2000]

            if on_agent_message and response_text.strip():
                instruction = job.action_message or "Process this polled data"
                message = (
                    f"[Poller '{job.name}' result]\n"
                    f"URL: {job.action_url}\n"
                    f"Status: {resp.status_code}\n"
                    f"Response:\n{response_text}\n\n"
                    f"Instructions: {instruction}"
                )
                await on_agent_message(message)

    except Exception as exc:
        logger.debug("Poller '%s' failed: %s", job.name, exc)


def create_poller_tool(manager: SchedulerManager) -> PollerTool:
    """Factory: create a poller tool backed by an existing scheduler manager."""
    return PollerTool(manager)
