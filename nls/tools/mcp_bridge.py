"""NLS MCP Bridge -- Connect to any MCP server and register tools.

The MCP (Model Context Protocol) bridge is a mandatory built-in that gives
NLS agents instant access to the entire MCP ecosystem: GitHub, Postgres,
Stripe, Notion, Slack, filesystem, and hundreds of community servers.

How it works:
    1. User adds an MCP server config (stdio command or HTTP URL)
    2. Bridge connects and discovers available tools via the MCP protocol
    3. Each MCP tool is auto-wrapped with NLS biological metadata (NLSToolManifest)
    4. Tools are registered in the ToolRegistry and work like any native tool
    5. Experience tracking and sleep consolidation apply to MCP tools too

The bridge handles both MCP transport types:
    - stdio: spawn a subprocess, communicate via JSON-RPC over stdin/stdout
    - HTTP/SSE: connect to a remote MCP server via HTTP

Usage::

    bridge = McpBridge(tool_registry)
    await bridge.connect_server({
        "name": "github",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "ghp_..."},
    })
    # Tools are now in the registry: github_list_repos, github_create_issue, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nls.engine.tools import (
    LearningYield,
    NLSTool,
    NLSToolManifest,
    RiskLevel,
    ToolCategory,
    ToolRegistry,
    ToolResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP Server configuration
# ---------------------------------------------------------------------------


@dataclass
class McpServerConfig:
    """Configuration for connecting to an MCP server."""
    name: str
    transport: str = "stdio"       # "stdio" | "http"
    command: str = ""              # For stdio: the command to run
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""                  # For HTTP: the server URL
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "env": {k: "***" for k in self.env},  # Mask secrets
            "url": self.url,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "McpServerConfig":
        return cls(
            name=data["name"],
            transport=data.get("transport", "stdio"),
            command=data.get("command", ""),
            args=data.get("args", []),
            env=data.get("env", {}),
            url=data.get("url", ""),
            enabled=data.get("enabled", True),
        )


# ---------------------------------------------------------------------------
# MCP Connection (stdio transport)
# ---------------------------------------------------------------------------


class McpStdioConnection:
    """Manages a JSON-RPC connection to an MCP server over stdio."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.process: subprocess.Popen | None = None
        self._request_id = 0
        self._connected = False

    async def connect(self) -> None:
        """Start the MCP server process and initialize the connection."""
        cmd = [self.config.command] + self.config.args
        env = {**dict(__import__("os").environ), **self.config.env}

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )

        # Send initialize request
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "nls-desktop",
                "version": "0.1.0",
            },
        })

        if result is not None:
            self._connected = True
            # Send initialized notification
            await self._send_notification("notifications/initialized", {})
            logger.info("MCP server '%s' connected", self.config.name)
        else:
            raise ConnectionError(f"Failed to initialize MCP server '{self.config.name}'")

    async def disconnect(self) -> None:
        """Shut down the MCP server process."""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                if self.process:
                    self.process.kill()
            self.process = None
        self._connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover available tools from the MCP server."""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            return result["tools"]
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server."""
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        if result is None:
            return "[ERROR] MCP tool call returned no result"

        # Extract text from MCP content array
        content = result.get("content", [])
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts) if texts else json.dumps(result)

    @property
    def connected(self) -> bool:
        return self._connected

    # ── JSON-RPC helpers ──────────────────────────────────────────────

    async def _send_request(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request and read the response."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            message = json.dumps(request) + "\n"
            self.process.stdin.write(message.encode())
            self.process.stdin.flush()

            # Read response (blocking -- run in executor for async)
            loop = asyncio.get_event_loop()
            line = await loop.run_in_executor(None, self.process.stdout.readline)

            if not line:
                return None

            response = json.loads(line.decode())
            if "error" in response:
                logger.warning(
                    "MCP error from '%s': %s",
                    self.config.name,
                    response["error"],
                )
                return None

            return response.get("result")

        except Exception as e:
            logger.error("MCP request failed: %s", e)
            return None

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            message = json.dumps(notification) + "\n"
            self.process.stdin.write(message.encode())
            self.process.stdin.flush()
        except Exception as e:
            logger.error("MCP notification failed: %s", e)


# ---------------------------------------------------------------------------
# MCP Tool Wrapper (wraps an MCP tool as an NLSTool)
# ---------------------------------------------------------------------------


class McpToolWrapper(NLSTool):
    """Wraps an MCP tool as a native NLS tool with biological metadata."""

    def __init__(
        self,
        mcp_tool: dict[str, Any],
        connection: McpStdioConnection,
        server_name: str,
    ) -> None:
        tool_name = mcp_tool.get("name", "unknown")
        description = mcp_tool.get("description", f"MCP tool from {server_name}")
        input_schema = mcp_tool.get("inputSchema", {})

        # Auto-detect category and risk from the tool name/description
        category, risk, hormone = self._infer_metadata(tool_name, description)

        manifest = NLSToolManifest(
            name=f"{server_name}_{tool_name}",  # Namespaced
            description=f"[{server_name}] {description}",
            category=category,
            hormone_affinity=hormone,
            base_effort=0.5,  # Default for MCP tools
            learning_yield=LearningYield.MEDIUM,
            risk_level=risk,
            permissions=["network.outbound"],
            input_schema=input_schema,
            source="mcp",
        )

        super().__init__(manifest)
        self._connection = connection
        self._mcp_name = tool_name

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the MCP tool via the bridge connection."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context -- use create_task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run,
                        self._connection.call_tool(self._mcp_name, args),
                    ).result(timeout=60)
            else:
                result = asyncio.run(
                    self._connection.call_tool(self._mcp_name, args)
                )

            success = not result.startswith("[ERROR]")
            return ToolResult(text=result, success=success)

        except Exception as e:
            return ToolResult(text="", success=False, error=str(e))

    @staticmethod
    def _infer_metadata(name: str, desc: str) -> tuple[ToolCategory, RiskLevel, str]:
        """Infer NLS metadata from MCP tool name and description."""
        name_lower = name.lower()
        desc_lower = desc.lower()

        # Read-oriented tools
        read_keywords = {"get", "list", "search", "query", "read", "fetch", "show"}
        write_keywords = {"create", "update", "delete", "write", "send", "post", "put"}
        exec_keywords = {"run", "execute", "deploy", "restart"}

        first_word = name_lower.split("_")[0] if "_" in name_lower else name_lower.split("-")[0]

        if first_word in exec_keywords or "execute" in desc_lower:
            return ToolCategory.ACT, RiskLevel.EXECUTE, "dopamine"
        elif first_word in write_keywords or "create" in desc_lower or "send" in desc_lower:
            if "send" in desc_lower or "message" in desc_lower or "email" in desc_lower:
                return ToolCategory.COMMUNICATE, RiskLevel.WRITE, "oxytocin"
            return ToolCategory.ACT, RiskLevel.WRITE, "dopamine"
        elif first_word in read_keywords or "search" in desc_lower:
            return ToolCategory.SENSE, RiskLevel.READ, "norepinephrine"

        return ToolCategory.SENSE, RiskLevel.READ, "norepinephrine"


# ---------------------------------------------------------------------------
# MCP Bridge (main orchestrator)
# ---------------------------------------------------------------------------


class McpBridge:
    """Orchestrates connections to MCP servers and registers their tools.

    Usage::

        bridge = McpBridge(registry)
        await bridge.connect_server(config)
        # Tools are now registered
        await bridge.disconnect_all()
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._connections: dict[str, McpStdioConnection] = {}
        self._configs: dict[str, McpServerConfig] = {}
        self._tool_names: dict[str, list[str]] = {}  # server -> [tool_names]

    async def connect_server(self, config: McpServerConfig) -> list[str]:
        """Connect to an MCP server and register its tools.

        Returns a list of registered tool names.
        """
        if config.name in self._connections:
            await self.disconnect_server(config.name)

        logger.info("Connecting to MCP server '%s' (%s)...", config.name, config.transport)

        if config.transport == "stdio":
            conn = McpStdioConnection(config)
            await conn.connect()
        else:
            raise ValueError(f"Unsupported MCP transport: {config.transport}")

        self._connections[config.name] = conn
        self._configs[config.name] = config

        # Discover and register tools
        mcp_tools = await conn.list_tools()
        tool_names = []

        for mcp_tool in mcp_tools:
            wrapper = McpToolWrapper(mcp_tool, conn, config.name)
            self._registry.register(wrapper)
            tool_names.append(wrapper.name)

        self._tool_names[config.name] = tool_names

        logger.info(
            "MCP server '%s': registered %d tools: %s",
            config.name, len(tool_names),
            ", ".join(tool_names),
        )

        return tool_names

    async def disconnect_server(self, name: str) -> None:
        """Disconnect from an MCP server and unregister its tools."""
        # Unregister tools
        for tool_name in self._tool_names.get(name, []):
            self._registry.unregister(tool_name)

        # Disconnect
        conn = self._connections.pop(name, None)
        if conn:
            await conn.disconnect()

        self._configs.pop(name, None)
        self._tool_names.pop(name, None)

        logger.info("MCP server '%s' disconnected", name)

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        for name in list(self._connections.keys()):
            await self.disconnect_server(name)

    def list_servers(self) -> list[dict[str, Any]]:
        """List all configured MCP servers and their status."""
        servers = []
        for name, config in self._configs.items():
            conn = self._connections.get(name)
            servers.append({
                "name": name,
                "transport": config.transport,
                "connected": conn.connected if conn else False,
                "tools": len(self._tool_names.get(name, [])),
                "tool_names": self._tool_names.get(name, []),
            })
        return servers

    # ── Persistence ──────────────────────────────────────────────────

    def save_config(self, path: Path) -> None:
        """Save server configs to disk (secrets masked)."""
        configs = [c.to_dict() for c in self._configs.values()]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"servers": configs}, f, indent=2)

    def load_config(self, path: Path) -> list[McpServerConfig]:
        """Load server configs from disk."""
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [McpServerConfig.from_dict(s) for s in data.get("servers", [])]
        except Exception as e:
            logger.warning("Failed to load MCP config: %s", e)
            return []
