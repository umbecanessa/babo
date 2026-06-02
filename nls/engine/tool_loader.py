"""Load tool definitions from nls/config/tools/*.json into a ToolRegistry."""

from __future__ import annotations

import importlib
import json
import logging
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


def _parse_enum(value: str, enum_cls: type, default: Any) -> Any:
    try:
        return enum_cls(value)
    except Exception:
        return default


class _HandlerTool(NLSTool):
    """Wraps a Python handler class with an execute(args) method."""

    def __init__(self, manifest: NLSToolManifest, handler_cls: type) -> None:
        super().__init__(manifest)
        self._handler = handler_cls()

    def execute(self, args: dict[str, Any]) -> ToolResult:
        raw = self._handler.execute(args)
        if isinstance(raw, ToolResult):
            return raw
        if hasattr(raw, "success"):
            return ToolResult(
                success=bool(raw.success),
                text=getattr(raw, "text", "") or "",
                error=getattr(raw, "error", None),
                metadata=getattr(raw, "metadata", {}) or {},
            )
        return ToolResult(success=True, text=str(raw))


def _resolve_handler(handler: str) -> type:
    if ":" in handler:
        module_path, class_name = handler.split(":", 1)
    else:
        module_path, class_name = handler.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _tool_from_json(data: dict[str, Any]) -> NLSTool | None:
    name = data.get("name")
    if not name:
        return None

    manifest = NLSToolManifest(
        name=name,
        description=data.get("description", ""),
        category=_parse_enum(data.get("category", "sense"), ToolCategory, ToolCategory.SENSE),
        hormone_affinity=data.get("hormone_affinity", "norepinephrine"),
        base_effort=float(data.get("base_effort", 0.1)),
        learning_yield=_parse_enum(
            data.get("learning_yield", "medium"), LearningYield, LearningYield.MEDIUM,
        ),
        risk_level=_parse_enum(data.get("risk_level", "read"), RiskLevel, RiskLevel.READ),
        permissions=list(data.get("permissions") or []),
        input_schema=dict(data.get("input_schema") or {}),
        source="json",
        version=data.get("version", "1.0.0"),
    )

    executor = data.get("executor") or {}
    handler = executor.get("handler")
    if handler:
        try:
            handler_cls = _resolve_handler(handler)
            return _HandlerTool(manifest, handler_cls)
        except Exception as exc:
            logger.warning("Tool %s: handler %s failed: %s", name, handler, exc)
            return None

    logger.debug("Tool %s: no handler in JSON — skipped", name)
    return None


def load_tool_from_json(registry: ToolRegistry, path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read tool JSON %s: %s", path, exc)
        return False
    tool = _tool_from_json(data)
    if tool is None:
        return False
    registry.register(tool)
    return True


def load_tools_from_directory(
    registry: ToolRegistry,
    directory: Path,
    *,
    enabled: set[str] | None = None,
) -> int:
    count = 0
    if not directory.exists():
        return 0
    for path in sorted(directory.glob("*.json")):
        if enabled is not None and path.stem not in enabled:
            continue
        if load_tool_from_json(registry, path):
            count += 1
    return count
