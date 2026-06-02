"""JSON-config tool registry for agency and MCP integrations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    SENSE = "sense"
    ACT = "act"
    CREATE = "create"
    COMMUNICATE = "communicate"
    AUTONOMIC = "autonomic"


class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    SAFE = "safe"


class LearningYield(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ToolResult:
    text: str = ""
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NLSToolManifest:
    name: str
    description: str = ""
    category: ToolCategory = ToolCategory.SENSE
    hormone_affinity: str = "norepinephrine"
    base_effort: float = 0.1
    learning_yield: LearningYield = LearningYield.MEDIUM
    risk_level: RiskLevel = RiskLevel.READ
    permissions: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = "native"
    version: str = "1.0.0"


class NLSTool:
    """Base class for registry-hosted tools."""

    def __init__(self, manifest: NLSToolManifest) -> None:
        self.manifest = manifest

    @property
    def name(self) -> str:
        return self.manifest.name

    def execute(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class ToolExperienceStore:
    """Optional per-agent tool usage stats (persisted by tool_loader)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load(self, path: Any) -> None:
        import json
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            return
        try:
            self._data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def save(self, path: Any) -> None:
        import json
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, NLSTool] = {}
        self.experience = ToolExperienceStore()

    def register(self, tool: NLSTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> NLSTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[NLSTool]:
        return list(self._tools.values())

    def get_tools_by_category(self, category: ToolCategory) -> list[str]:
        return [
            t.name for t in self._tools.values()
            if t.manifest.category == category
        ]


class WebBrowseTool(NLSTool):
    """Playwright-backed web search/browse (used by agency + JSON tool configs)."""

    def __init__(
        self,
        browser_engine: Any,
        *,
        max_results: int = 5,
        max_content_chars: int = 4000,
        max_pages_per_browse: int = 3,
    ) -> None:
        manifest = NLSToolManifest(
            name="web_search",
            description="Search and read web pages",
            category=ToolCategory.SENSE,
            hormone_affinity="norepinephrine",
            base_effort=0.4,
            learning_yield=LearningYield.HIGH,
            risk_level=RiskLevel.READ,
            permissions=["network.outbound"],
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "url": {"type": "string"},
                    "depth": {"type": "integer"},
                },
            },
        )
        super().__init__(manifest)
        self._browser = browser_engine
        self._max_results = max_results
        self._max_content_chars = max_content_chars
        self._max_pages = max_pages_per_browse

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        url = str(args.get("url") or "").strip()
        depth = int(args.get("depth") or 1)
        try:
            if url:
                page = self._browser.read_page(url)
                text = (page.text or "")[: self._max_content_chars]
            elif query:
                results = self._browser.search(
                    query, max_results=min(depth, self._max_results),
                )
                chunks: list[str] = []
                for hit in results[: self._max_pages]:
                    page = self._browser.read_page(hit.url)
                    chunks.append(
                        f"## {hit.title}\n{hit.url}\n{(page.text or '')[:self._max_content_chars]}"
                    )
                text = "\n\n".join(chunks) if chunks else "No results found."
            else:
                return ToolResult(success=False, error="query or url required")
            return ToolResult(success=True, text=text or "(no content)")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
