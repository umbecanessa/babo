"""Todo tool -- agent-facing CRUD with WM bridge and WebSocket broadcasting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult
from .store import TodoStore, PRIORITIES, STATUSES

logger = logging.getLogger(__name__)

_IDLE_TRIGGER = (
    "idle bored free time downtime nothing to do "
    "spare time when free when idle"
)


class TodoTool:
    """Per-agent todo list management tool (AgentTool protocol)."""

    def __init__(
        self, store: TodoStore, agent_id: str, app: Any,
        manager: TodoManager | None = None,
    ) -> None:
        self._store = store
        self._agent_id = agent_id
        self._app = app
        self._manager = manager

    # -- AgentTool protocol ------------------------------------------------

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Master task tracker — every unit of work should be a todo. "
            "RULES: (1) Always include a description when adding. "
            "(2) Call list first to avoid duplicates. "
            "(3) Decompose complex tasks into multiple focused todos. "
            "(4) Never complete a todo that has a linked plan — let "
            "plan(action='complete') auto-mark it done. "
            "For multi-step work, create a plan linked to the "
            "todo via plan(action='create', todo_id=<id>). "
            "Tasks marked idle_eligible are picked up during idle time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action", "title"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "add", "list", "get", "complete", "remove",
                        "update", "move", "next_idle", "triage",
                        "create_list", "list_lists",
                    ],
                    "description": "The operation to perform.",
                },
                "title": {
                    "type": "string",
                    "description": "Task title (required for 'add'). Pass '-' for non-add actions.",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed task description (required for 'add' — never leave empty).",
                },
                "priority": {
                    "type": "string",
                    "enum": list(PRIORITIES),
                    "description": "Task priority. Default: normal.",
                },
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": "Task status (for 'update'/'triage').",
                },
                "idle_eligible": {
                    "type": "boolean",
                    "description": (
                        "If true, the agent will work on this task during "
                        "idle time (autonomous dreams)."
                    ),
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Task ID. For add: optional custom ID (otherwise auto-generated). "
                        "For get/complete/remove/update/move/triage: required."
                    ),
                },
                "list_id": {
                    "type": "string",
                    "description": (
                        "Target list ID (for add/move/triage). "
                        "Default lists: inbox, projects, research, creative."
                    ),
                },
                "status_filter": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": "Filter by status (for 'list').",
                },
                "list_filter": {
                    "type": "string",
                    "description": "Filter by list ID (for 'list').",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization.",
                },
                "due_date": {
                    "type": "string",
                    "description": (
                        "Due date in ISO 8601 format "
                        "(e.g. '2026-03-15' or '2026-03-15T17:00:00'). "
                        "Use for deadline-sensitive tasks."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": "Progress notes (for 'update').",
                },
                "source": {
                    "type": "string",
                    "enum": ["user", "agent", "channel"],
                    "description": "Who created the task. Default: user.",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Link a plan to this todo (for 'update'). Auto-set by plan tool on create.",
                },
                "list_name": {
                    "type": "string",
                    "description": "Name for a new custom list (create_list).",
                },
                "list_icon": {
                    "type": "string",
                    "description": "Icon name for a new custom list.",
                },
                "list_color": {
                    "type": "string",
                    "description": "Hex color for a new custom list.",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        try:
            handler = {
                "add": self._add,
                "list": self._list,
                "get": self._get,
                "complete": self._complete,
                "remove": self._remove,
                "update": self._update,
                "move": self._move,
                "next_idle": self._next_idle,
                "triage": self._triage,
                "create_list": self._create_list,
                "list_lists": self._list_lists,
            }.get(action)
            if handler is None:
                return ToolResult(
                    content=f"Unknown action: {action}", is_error=True,
                )
            return await handler(params)
        except Exception as exc:
            logger.exception("Todo tool error (action=%s)", action)
            return ToolResult(content=f"Error: {exc}", is_error=True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _add(self, params: dict[str, Any]) -> ToolResult:
        title = params.get("title", "").strip()
        if not title:
            return ToolResult(content="Title is required.", is_error=True)

        description = params.get("description", "").strip()

        # Duplicate guard: reject exact match or high fuzzy similarity.
        title_lower = title.lower()
        title_tokens = set(title_lower.split())
        for existing in self._store.list_items():
            if existing.status in ("done", "cancelled"):
                continue
            existing_lower = existing.title.lower()
            if existing_lower == title_lower:
                return ToolResult(
                    content=(
                        f"Duplicate: an active todo already exists with this "
                        f"title — [{existing.id}] {existing.title} "
                        f"(status={existing.status}). Use that one instead, "
                        f"or pick a more specific title."
                    ),
                    is_error=True,
                )
            existing_tokens = set(existing_lower.split())
            if title_tokens and existing_tokens:
                jaccard = len(title_tokens & existing_tokens) / len(title_tokens | existing_tokens)
                if jaccard >= 0.6:
                    return ToolResult(
                        content=(
                            f"Near-duplicate: an active todo is very similar — "
                            f"[{existing.id}] {existing.title} "
                            f"(status={existing.status}, similarity={jaccard:.0%}). "
                            f"Use that one instead, or pick a clearly different title."
                        ),
                        is_error=True,
                    )

        status = params.get("status", "inbox")
        list_id = params.get("list_id", "inbox")
        idle_eligible = params.get("idle_eligible", False)

        # Auto-triage: if explicitly classified into a list or marked
        # idle-eligible, promote from "inbox" (untriaged) to "queued".
        if status == "inbox" and (list_id != "inbox" or idle_eligible):
            status = "queued"

        add_kwargs: dict[str, Any] = dict(
            title=title,
            description=description,
            priority=params.get("priority", "normal"),
            status=status,
            list_id=list_id,
            idle_eligible=idle_eligible,
            source=params.get("source", "user"),
            tags=params.get("tags", []),
            due_date=params.get("due_date", ""),
        )
        requested_id = params.get("id", "").strip()
        if requested_id and self._store.get(requested_id) is None:
            add_kwargs["id"] = requested_id

        item = self._store.add(**add_kwargs)

        if item.idle_eligible:
            self._sync_idle_intention()

        await self._broadcast("added", item)

        warn = ""
        if not description:
            warn = ("\n⚠ No description provided — consider updating this "
                    "todo with a description for clarity.")

        # Show board digest so the agent always sees what already exists.
        active = [
            i for i in self._store.list_items()
            if i.status not in ("done", "cancelled") and i.id != item.id
        ]
        board_digest = ""
        if active:
            board_lines = [f"\n--- Board ({len(active) + 1} active items) ---"]
            for a in active:
                plan_tag = f" [plan:{a.plan_id}]" if a.plan_id else ""
                team_tag = f" [team:{a.team_id}]" if a.team_id else ""
                board_lines.append(
                    f"  [{a.id}] {a.title} ({a.status}){plan_tag}{team_tag}"
                )
            board_lines.append(
                "Check these before adding more — reuse existing items "
                "when possible."
            )
            board_digest = "\n".join(board_lines)

        return ToolResult(
            content=(
                f"Added todo [{item.id}]: {item.title}\n"
                f"ID (use this exact value for plan todo_id): {item.id}\n"
                f"List: {item.list_id} | Priority: {item.priority} | "
                f"Idle eligible: {item.idle_eligible}{warn}{board_digest}"
            ),
            details={"todo_id": item.id, "action": "add"},
        )

    async def _list(self, params: dict[str, Any]) -> ToolResult:
        items = self._store.list_items(
            status=params.get("status_filter"),
            list_id=params.get("list_filter"),
        )
        if not items:
            return ToolResult(content="No todo items found.")

        lines: list[str] = []
        for item in items:
            idle_tag = " [IDLE]" if item.idle_eligible else ""
            due_tag = f" due:{item.due_date}" if item.due_date else ""
            plan_tag = f" [plan:{item.plan_id}]" if item.plan_id else ""
            team_tag = f" [team:{item.team_id}]" if item.team_id else ""
            dep_tag = f" [dep:{','.join(item.depends_on)}]" if item.depends_on else ""
            lines.append(
                f"[{item.id}] {item.title} "
                f"({item.list_id}, {item.priority}, {item.status})"
                f"{due_tag}{plan_tag}{team_tag}{dep_tag}{idle_tag}"
            )
        lines.append(f"\nTotal: {len(items)} items")
        return ToolResult(content="\n".join(lines))

    async def _get(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        item = self._store.get(item_id)
        if item is None:
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )
        parts = [
            f"ID: {item.id}",
            f"Title: {item.title}",
            f"List: {item.list_id}",
            f"Status: {item.status}",
            f"Priority: {item.priority}",
            f"Idle eligible: {item.idle_eligible}",
            f"Source: {item.source}",
        ]
        if item.due_date:
            parts.append(f"Due: {item.due_date}")
        if item.description:
            parts.append(f"Description: {item.description}")
        if item.tags:
            parts.append(f"Tags: {', '.join(item.tags)}")
        if item.notes:
            parts.append(f"Notes: {item.notes}")
        return ToolResult(content="\n".join(parts))

    async def _complete(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        item = self._store.get(item_id)
        if item is None:
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )

        # Guard: if a plan is linked, the plan completion should drive this.
        if item.plan_id:
            return ToolResult(
                content=(
                    f"Todo [{item.id}] has a linked plan ({item.plan_id}). "
                    f"Complete the plan instead — plan(action='complete') will "
                    f"auto-mark this todo as done. Work through each plan step "
                    f"individually before completing."
                ),
                is_error=True,
            )

        item = self._store.update(item_id, status="done")
        self._sync_idle_intention()
        await self._broadcast("completed", item)
        return ToolResult(
            content=f"Completed: [{item.id}] {item.title}",
            details={"todo_id": item.id, "action": "complete"},
        )

    async def _remove(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        item = self._store.get(item_id)
        if item is None or not self._store.remove(item_id):
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )
        self._sync_idle_intention()
        await self._broadcast("removed", item)
        return ToolResult(
            content=f"Removed todo '{item_id}'.",
            details={"todo_id": item_id, "action": "remove"},
        )

    async def _update(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        updates: dict[str, Any] = {}
        for key in (
            "title", "description", "priority", "idle_eligible",
            "tags", "notes", "status", "due_date", "plan_id",
        ):
            if key in params:
                updates[key] = params[key]
        if not updates:
            return ToolResult(content="No fields to update.", is_error=True)

        item = self._store.update(item_id, **updates)
        if item is None:
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )

        if "idle_eligible" in updates or "status" in updates:
            self._sync_idle_intention()

        await self._broadcast("updated", item)
        return ToolResult(
            content=f"Updated [{item.id}]: {item.title} ({item.status})",
            details={"todo_id": item.id, "action": "update"},
        )

    async def _move(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        list_id = params.get("list_id", "")
        if not list_id:
            return ToolResult(
                content="list_id is required for move.", is_error=True,
            )
        item = self._store.move(item_id, list_id)
        if item is None:
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )
        await self._broadcast("updated", item)
        return ToolResult(
            content=f"Moved [{item.id}] to list '{list_id}'.",
            details={"todo_id": item.id, "action": "move"},
        )

    async def _next_idle(self, _params: dict[str, Any]) -> ToolResult:
        item = self._store.next_idle_task()
        if item is None:
            return ToolResult(content="No idle-eligible tasks pending.")
        return ToolResult(
            content=(
                f"Next idle task [{item.id}]: {item.title}\n"
                f"Priority: {item.priority} | List: {item.list_id}\n"
                f"Description: {item.description or '(none)'}\n"
                f"Notes: {item.notes or '(none)'}"
            ),
            details={"todo_id": item.id},
        )

    async def _triage(self, params: dict[str, Any]) -> ToolResult:
        item_id = params.get("id", "")
        updates: dict[str, Any] = {}
        for key in ("list_id", "priority", "idle_eligible", "status", "tags"):
            if key in params:
                updates[key] = params[key]
        if "status" not in updates:
            updates["status"] = "queued"

        item = self._store.update(item_id, **updates)
        if item is None:
            return ToolResult(
                content=f"No todo with id '{item_id}'.", is_error=True,
            )
        if "idle_eligible" in updates or "status" in updates:
            self._sync_idle_intention()

        await self._broadcast("updated", item)
        return ToolResult(
            content=(
                f"Triaged [{item.id}]: {item.title} → "
                f"{item.list_id} ({item.priority}, {item.status})"
            ),
            details={"todo_id": item.id, "action": "triage"},
        )

    async def _create_list(self, params: dict[str, Any]) -> ToolResult:
        name = params.get("list_name", "").strip()
        if not name:
            return ToolResult(
                content="list_name is required.", is_error=True,
            )
        lst = self._store.create_list(
            name=name,
            icon=params.get("list_icon", ""),
            color=params.get("list_color", "#94a3b8"),
        )
        return ToolResult(
            content=f"Created list [{lst.id}]: {lst.name}",
            details={"list_id": lst.id, "action": "create_list"},
        )

    async def _list_lists(self, _params: dict[str, Any]) -> ToolResult:
        lists = self._store.get_lists()
        if not lists:
            return ToolResult(content="No lists defined.")
        lines = [
            f"[{lst.id}] {lst.name} — {lst.description}" for lst in lists
        ]
        return ToolResult(content="\n".join(lines))

    # ------------------------------------------------------------------
    # WM + WebSocket helpers (delegate to manager when available)
    # ------------------------------------------------------------------

    def _sync_idle_intention(self) -> None:
        if self._manager:
            self._manager.sync_idle_intention(self._agent_id)

    async def _broadcast(self, action: str, item: Any) -> None:
        if self._manager:
            await self._manager.broadcast(self._agent_id, action, item)


class TodoManager:
    """Creates per-agent TodoTool instances and manages stores.

    Also exposes ``sync_idle_intention`` and ``broadcast`` so that
    the REST routes can call them after mutations.
    """

    def __init__(self, app: Any, ctx: Any) -> None:
        self._app = app
        self._ctx = ctx
        self._stores: dict[str, TodoStore] = {}

    async def startup(self) -> None:
        logger.info("Todo list skill started")
        self._resync_all_idle_intentions()

    def _resync_all_idle_intentions(self) -> None:
        """Re-sync idle intentions to WM for all agents on startup/wake.

        After restart or sleep, the WM prospective intention may be stale
        or missing while the todo store still has idle-eligible tasks.
        """
        try:
            am = getattr(self._app.state, "agent_manager", None)
            if am is None:
                return
            loaded = getattr(am, "get_loaded_runtimes", None)
            if loaded is None:
                return
            for agent_id in loaded().keys():
                store = self.get_store(agent_id)
                if store.next_idle_task() is not None:
                    self.sync_idle_intention(agent_id)
                    logger.debug(
                        "Todo: re-synced idle intention for agent %s",
                        agent_id,
                    )
        except Exception as exc:
            logger.debug("Todo startup re-sync failed: %s", exc)

    def get_store(self, agent_id: str) -> TodoStore:
        if agent_id not in self._stores:
            self._stores[agent_id] = TodoStore(
                data_dir=self._ctx.data_dir,
                agent_id=agent_id,
            )
        return self._stores[agent_id]

    def create_tool(self, agent_id: str) -> TodoTool:
        store = self.get_store(agent_id)
        return TodoTool(
            store=store, agent_id=agent_id,
            app=self._app, manager=self,
        )

    # -- Shared helpers (used by both tool and REST routes) ---------------

    def sync_idle_intention(self, agent_id: str) -> None:
        """Keep the top idle-eligible todo in WM as both intention and goal.

        Adds a prospective intention (fires on idle trigger words) AND a
        tactical goal so the drive system's cross-drive boost recognises
        there is work to do and the competence drive fires sooner.
        """
        try:
            am = getattr(self._app.state, "agent_manager", None)
            if am is None:
                return
            runtime = am.get_runtime(agent_id)
            if runtime is None:
                return
            wm = getattr(runtime, "working_memory", None)
            if wm is None:
                return

            wm.remove_intentions_where(
                lambda i: getattr(i, "source", "") == "todo-list"
            )
            wm.remove_goals_where(
                lambda g: getattr(g, "source", "") == "todo-list"
            )

            store = self.get_store(agent_id)
            next_task = store.next_idle_task()
            if next_task is not None:
                desc_suffix = (
                    f" — {next_task.description}"
                    if next_task.description else ""
                )
                notes_suffix = (
                    f"\nPrevious progress: {next_task.notes}"
                    if next_task.notes else ""
                )
                task_content = (
                    f"Work on todo [{next_task.id}]: "
                    f"{next_task.title}{desc_suffix}{notes_suffix}"
                )
                wm.add_intention(
                    content=task_content,
                    trigger=_IDLE_TRIGGER,
                    source="todo-list",
                )
                wm.add_goal(
                    level="tactical",
                    content=task_content,
                    source="todo-list",
                )
        except Exception as exc:
            logger.debug("Todo skill: WM sync failed: %s", exc)

    async def broadcast(self, agent_id: str, action: str, item: Any) -> None:
        """Push a ``todo_update`` event to connected frontends."""
        try:
            cm = getattr(self._app.state, "connection_manager", None)
            if cm is None:
                return
            await cm.broadcast(agent_id, {
                "type": "todo_update",
                "action": action,
                "item": item.to_dict() if hasattr(item, "to_dict") else {},
            })
        except Exception as exc:
            logger.debug("Todo broadcast failed: %s", exc)


class TodoReadOnlyTool:
    """Read-only view of the todo board for sub-agents / delegates.

    Delegates can list/get todos to understand the board, but cannot
    create, update, complete, or remove them.  The orchestrator owns
    the todo lifecycle.
    """

    _ALLOWED_ACTIONS = frozenset({"list", "get", "list_lists"})

    def __init__(self, store: TodoStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Read-only view of the master todo board. "
            "You can list or get todos to understand the task context, "
            "but you CANNOT add, update, complete, or remove them. "
            "The orchestrator manages the board — you execute."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "list_lists"],
                    "description": "Read-only operation: list all todos, get one by id, or list available lists.",
                },
                "id": {
                    "type": "string",
                    "description": "Todo ID (for 'get').",
                },
            },
        }

    async def execute(self, params: dict[str, Any], **kw: Any) -> ToolResult:
        action = (params.get("action") or "").strip().lower()
        if action not in self._ALLOWED_ACTIONS:
            return ToolResult(
                content=(
                    f"READ-ONLY: action '{action}' is not allowed for sub-agents. "
                    "You can only use 'list', 'get', or 'list_lists'. "
                    "The orchestrator manages the todo board."
                ),
                is_error=True,
            )
        if action == "list":
            items = self._store.list_items()
            if not items:
                return ToolResult(content="No todos found.")
            lines = []
            for it in items:
                line = f"[{it.status}] {it.title} (id:{it.id}"
                if it.priority != "normal":
                    line += f" !!{it.priority}"
                if it.plan_id:
                    line += f" [plan:{it.plan_id}]"
                line += ")"
                lines.append(line)
            return ToolResult(content="\n".join(lines))
        if action == "get":
            item_id = (params.get("id") or "").strip()
            if not item_id:
                return ToolResult(content="'id' is required for 'get'.", is_error=True)
            item = self._store.get_item(item_id)
            if not item:
                return ToolResult(content=f"No todo with id '{item_id}'.", is_error=True)
            return ToolResult(content=str(item.to_dict()))
        if action == "list_lists":
            lists = self._store.list_lists()
            return ToolResult(content="\n".join(
                f"{l.name} (id:{l.id})" for l in lists
            ) if lists else "No lists.")
        return ToolResult(content="Unknown action.", is_error=True)
