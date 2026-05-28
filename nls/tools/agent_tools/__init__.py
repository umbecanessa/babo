"""NLS Agent Tools -- Minimal composable tool system.

Core tools that give the agent the power to do anything:

    read              -- Read file contents (text, PDF, Office, audio, archives)
    write             -- Create/overwrite files
    edit              -- Surgical find-and-replace
    grep              -- Fast regex/text search across files (ripgrep or Python)
    glob              -- Find files by name/path pattern
    list_dir          -- Structured directory listing with sizes and tree view
    delete_file       -- Delete a file or directory
    move_file         -- Move or rename a file or directory
    semantic_search    -- Semantic codebase search via nomic-embed-code (local or runtime fallback)
    bash              -- Execute any shell command
    web_search        -- Search the web for real-time information
    web_fetch         -- Fetch a URL and return readable text
    browser           -- Chromium automation (in-app webview via CDP and/or
                         standalone Playwright window; same tool name)
    offer_download    -- Offer a workspace file for the user to download
    server_install    -- Install a pip package into the server runtime
    project_install   -- Install a dependency into the project (.venv / npm)
    request_restart   -- Gracefully restart the server gateway
    scheduler         -- Create cron/interval/one-shot scheduled jobs
    poller            -- Create HTTP polling jobs (monitor APIs, drain queues)

Usage::

    from nls.tools.agent_tools import create_coding_tools, tools_to_openai_schema

    tools = create_coding_tools("/home/user/project")
    schemas = tools_to_openai_schema(tools)

    # Execute a tool call from vLLM
    result = await execute_tool_call(tools, "bash", {"command": "ls -la"})
"""

from __future__ import annotations

import logging
import os
import threading

_logger = logging.getLogger(__name__)


class SharedCWD:
    """Mutable CWD holder shared by all file-based tools.

    When bash detects a CWD change (via the pwd sentinel), it updates
    this object. read/write/edit resolve relative paths against
    ``self.path``, so they always see the current working directory.
    """

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        self.path = path

    def __str__(self) -> str:
        return self.path


class FileStateCache:
    """Thread-safe, per-caller mtime cache for read-before-edit enforcement.

    Tracks mtime snapshots **per caller** (identified by asyncio Task or
    thread), so concurrent delegates cannot bypass each other's writes.

    On ``read_file``: call :meth:`record` to snapshot the file's mtime
    for the calling task.
    On ``write_file`` / ``edit_file``: call :meth:`check` — returns an
    error string if the file was never read by this caller or changed
    since this caller's last read.  After a successful write, call
    :meth:`update` to refresh the caller's snapshot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reads: dict[tuple[str, int], float] = {}

    @staticmethod
    def _caller_id() -> int:
        import asyncio
        try:
            task = asyncio.current_task()
            if task is not None:
                return id(task)
        except RuntimeError:
            pass
        return threading.get_ident()

    def record(self, path: str) -> None:
        """Record the current mtime for *path* (call after a successful read)."""
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return
        with self._lock:
            self._reads[(path, self._caller_id())] = mt

    def check(self, path: str) -> str | None:
        """Return an error message if *path* is unsafe to write, else ``None``.

        Unsafe means: (a) the caller never read the file, or (b) the file
        changed on disk since the caller's last read.  New files (path does
        not exist) are always allowed.
        """
        cid = self._caller_id()
        with self._lock:
            recorded = self._reads.get((path, cid))
        if recorded is None:
            if os.path.exists(path):
                return (
                    f"MUST READ FIRST: {path} exists but you haven't read it "
                    f"in this session. Call read() on the file before editing "
                    f"to avoid overwriting concurrent changes."
                )
            return None
        try:
            current = os.path.getmtime(path)
        except OSError:
            return None
        if abs(current - recorded) > 0.001:
            return (
                f"STALE FILE: {path} was modified since you last read it "
                f"(recorded mtime {recorded}, current {current}). "
                f"Re-read the file before editing to avoid overwriting "
                f"concurrent changes."
            )
        return None

    def update(self, path: str) -> None:
        """Update the caller's cached mtime after a successful write."""
        cid = self._caller_id()
        try:
            mt = os.path.getmtime(path)
        except OSError:
            with self._lock:
                self._reads.pop((path, cid), None)
            return
        with self._lock:
            self._reads[(path, cid)] = mt

    def clear(self) -> None:
        with self._lock:
            self._reads.clear()


from .base import (
    AgentTool,
    ToolResult,
    tool_to_openai_schema,
    tools_to_openai_schema,
)
from .bash import BashTool, create_bash_tool
from .browser_adapter import BrowserAdapterTool, create_browser_tool
from .delete_file import DeleteFileTool, create_delete_file_tool
from .edit import EditTool, create_edit_tool
from .file_ledger import FileLedger, FileHistoryTool
from .email_ledger import EmailLedger, EmailHistoryTool
from .glob import GlobTool, create_glob_tool
from .grep import GrepTool, create_grep_tool
from .list_dir import ListDirTool, create_list_dir_tool
from .move_file import MoveFileTool, create_move_file_tool
from .offer_download import OfferDownloadTool, create_offer_download_tool
from .poller import PollerTool, create_poller_tool
from .read import ReadTool, create_read_tool
from .request_restart import RequestRestartTool, create_request_restart_tool
from .scheduler import (
    SchedulerTool,
    SchedulerManager,
    ScheduledJob,
    create_scheduler_tool,
)
from .semantic_search import SemanticSearchTool, create_semantic_search_tool
from .project_install import ProjectInstallTool, create_project_install_tool
from .server_install import ServerInstallTool, create_server_install_tool
from .vision import VisionTool, create_vision_tool
from .web_fetch import WebFetchTool, create_web_fetch_tool
from .web_search import WebSearchTool, create_web_search_tool
from .plan import PlanTool, create_plan_tool
from .task_complete import TaskCompleteTool, create_task_complete_tool
from .write import WriteTool, create_write_tool
from .discover_tools import DiscoverToolsTool, create_discover_tools_tool


def create_coding_tools(
    cwd: str,
    bash_timeout: int | None = None,
    blocked_commands: list[str] | None = None,
    on_bash_output=None,
    browser_headless: bool = False,
    on_browser_navigation=None,
    on_browser_auth_request=None,
    browser_profile_dir: str = "",
    browser_cdp_url: str = "",
    runtime_url: str = "",
    data_dir: str = "",
    agent_id: str = "",
    gpu_worker_secret: str = "",
) -> tuple[list[AgentTool], SchedulerManager | None]:
    """Create the core tools configured for a working directory.

    Parameters
    ----------
    cwd : str
        Working directory for all tools.
    bash_timeout : int | None
        Default timeout for bash commands (None = no timeout).
    blocked_commands : list[str] | None
        Command patterns to block in bash.
    on_bash_output : callable | None
        Async callback ``(chunk: str) -> None`` for live bash output
        streaming.
    browser_headless : bool
        Run the Playwright browser in headless mode (default: False,
        visible browser window so the user can watch).
    on_browser_navigation : callable | None
        Callback ``(event, url, title) -> None`` for browser navigation
        events (so the frontend can show browsing status).
    browser_profile_dir : str
        Directory for persistent browser profile.  When set, cookies
        and login sessions survive across agent restarts, so the agent
        stays logged into services like Google, Discord, Slack, etc.
    browser_cdp_url : str
        CDP endpoint URL (e.g. ``http://127.0.0.1:9245``).  When set,
        the browser tool connects to an existing browser (the Electron
        app's webview) via Chrome DevTools Protocol instead of
        launching a standalone Chromium window.
    runtime_url : str
        URL of the runtime embedding service.  When provided, enables
        the vision tool (image understanding + OCR via Moondream) and
        the semantic_search tool (remote embedding fallback via nomic-embed-code).

    Returns
    -------
    tuple[list[AgentTool], SchedulerManager | None]
        Core tools and the scheduler manager (if data_dir is set).
    """
    shared_cwd = SharedCWD(cwd)
    file_cache = FileStateCache()
    scheduler_manager: SchedulerManager | None = None
    tools: list[AgentTool] = [
        create_read_tool(cwd, shared_cwd=shared_cwd, file_state_cache=file_cache),
        create_write_tool(cwd, shared_cwd=shared_cwd, file_state_cache=file_cache),
        create_edit_tool(cwd, shared_cwd=shared_cwd, file_state_cache=file_cache),
        create_grep_tool(cwd, shared_cwd=shared_cwd),
        create_glob_tool(cwd, shared_cwd=shared_cwd),
        create_list_dir_tool(cwd, shared_cwd=shared_cwd),
        create_delete_file_tool(cwd, shared_cwd=shared_cwd),
        create_move_file_tool(cwd, shared_cwd=shared_cwd),
        create_semantic_search_tool(
            cwd,
            runtime_url=runtime_url,
            gpu_worker_secret=gpu_worker_secret,
            shared_cwd=shared_cwd,
        ),
        create_bash_tool(
            cwd,
            default_timeout=bash_timeout,
            blocked_patterns=blocked_commands,
            on_output=on_bash_output,
            shared_cwd=shared_cwd,
        ),
        create_web_search_tool(),
        create_web_fetch_tool(),
        create_browser_tool(
            headless=browser_headless,
            on_navigation=on_browser_navigation,
            user_data_dir=browser_profile_dir,
            workspace_path=cwd,
            cdp_url=browser_cdp_url,
            request_auth=on_browser_auth_request,
        ),
        create_offer_download_tool(cwd),
        create_server_install_tool(),
        create_project_install_tool(cwd, shared_cwd=shared_cwd),
        create_request_restart_tool(data_dir=data_dir, agent_id=agent_id),
        create_discover_tools_tool(),
    ]

    if data_dir:
        sched_tool, scheduler_manager = create_scheduler_tool(data_dir)
        tools.append(sched_tool)
        tools.append(create_poller_tool(scheduler_manager))

    if runtime_url:
        tools.append(create_vision_tool(runtime_url))

    return tools, scheduler_manager


async def execute_tool_call(
    tools: list[AgentTool],
    tool_name: str,
    params: dict,
    signal=None,
) -> ToolResult:
    """Execute a tool call by name from a list of tools.

    Parameters
    ----------
    tools : list[AgentTool]
        Available tools.
    tool_name : str
        Name of the tool to execute.
    params : dict
        Parameters for the tool.
    signal : asyncio.Event | None
        Abort signal.

    Returns
    -------
    ToolResult
        The execution result.

    Raises
    ------
    KeyError
        If the tool is not found.
    """
    tool_map = {t.name: t for t in tools}
    tool = tool_map.get(tool_name)
    if tool is None:
        available = ", ".join(tool_map.keys())
        return ToolResult(
            content=f"Error: Tool '{tool_name}' not found. Available: {available}",
            is_error=True,
        )
    return await tool.execute(params, signal)


__all__ = [
    "SharedCWD",
    "AgentTool",
    "ToolResult",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "FileLedger",
    "FileHistoryTool",
    "EmailLedger",
    "EmailHistoryTool",
    "GrepTool",
    "GlobTool",
    "ListDirTool",
    "DeleteFileTool",
    "MoveFileTool",
    "SemanticSearchTool",
    "BashTool",
    "WebSearchTool",
    "WebFetchTool",
    "BrowserAdapterTool",
    "VisionTool",
    "OfferDownloadTool",
    "ServerInstallTool",
    "ProjectInstallTool",
    "RequestRestartTool",
    "SchedulerTool",
    "SchedulerManager",
    "ScheduledJob",
    "PollerTool",
    "PlanTool",
    "TaskCompleteTool",
    "create_plan_tool",
    "create_task_complete_tool",
    "create_read_tool",
    "create_write_tool",
    "create_edit_tool",
    "create_grep_tool",
    "create_glob_tool",
    "create_list_dir_tool",
    "create_delete_file_tool",
    "create_move_file_tool",
    "create_semantic_search_tool",
    "create_bash_tool",
    "create_web_search_tool",
    "create_web_fetch_tool",
    "create_browser_tool",
    "create_vision_tool",
    "create_offer_download_tool",
    "create_server_install_tool",
    "create_project_install_tool",
    "create_request_restart_tool",
    "create_scheduler_tool",
    "create_poller_tool",
    "create_coding_tools",
    "execute_tool_call",
    "tool_to_openai_schema",
    "tools_to_openai_schema",
]
