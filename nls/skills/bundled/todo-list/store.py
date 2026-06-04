"""Persistent per-agent todo storage with multi-list support."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("inbox", "queued", "in_progress", "done", "deferred")

_PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}

DEFAULT_LISTS: list[dict[str, Any]] = [
    {
        "id": "inbox",
        "name": "Inbox",
        "icon": "inbox",
        "description": "Default landing for new tasks",
        "color": "#94a3b8",
        "sort_order": 0,
    },
    {
        "id": "projects",
        "name": "Projects",
        "icon": "code",
        "description": "Dev and project work",
        "color": "#818cf8",
        "sort_order": 1,
    },
    {
        "id": "research",
        "name": "Research",
        "icon": "search",
        "description": "Things to learn and look into",
        "color": "#38bdf8",
        "sort_order": 2,
    },
    {
        "id": "creative",
        "name": "Creative",
        "icon": "sparkles",
        "description": "Fun and experimental builds",
        "color": "#c084fc",
        "sort_order": 3,
    },
]


@dataclass
class TodoList:
    id: str = ""
    name: str = ""
    icon: str = ""
    description: str = ""
    color: str = "#94a3b8"
    sort_order: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TodoList:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class TodoItem:
    id: str = ""
    list_id: str = "inbox"
    title: str = ""
    description: str = ""
    priority: str = "normal"
    status: str = "inbox"
    idle_eligible: bool = False
    source: str = "user"
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    due_date: str = ""  # ISO 8601 date or datetime (e.g. "2026-03-15" or "2026-03-15T17:00:00")
    plan_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float | None = None

    # Team integration fields
    team_id: str = ""
    plan_step_id: str = ""
    parent_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    delegate_number: int | None = None

    # Squad integration fields
    squad_id: str = ""
    squad_inbox_id: str = ""
    assigner_agent_id: str = ""
    assignee_agent_id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TodoItem:
        d = dict(d)
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


class TodoStore:
    """Per-agent persistent todo list backed by JSON.

    Data lives at ``{data_dir}/{agent_id}/todos.json`` and holds both
    the list definitions and all items in a single file for simplicity.
    """

    def __init__(self, data_dir: Path, agent_id: str) -> None:
        self._dir = data_dir / agent_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "todos.json"
        self._lists: list[TodoList] = []
        self._items: list[TodoItem] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._lists = [TodoList.from_dict(d) for d in raw.get("lists", [])]
                self._items = [TodoItem.from_dict(d) for d in raw.get("items", [])]
            except (json.JSONDecodeError, OSError):
                self._lists = []
                self._items = []

        if not self._lists:
            self._lists = [TodoList.from_dict(d) for d in DEFAULT_LISTS]
            self._save()

    def _save(self) -> None:
        payload = {
            "version": "1.0",
            "lists": [lst.to_dict() for lst in self._lists],
            "items": [item.to_dict() for item in self._items],
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def get_lists(self) -> list[TodoList]:
        return sorted(self._lists, key=lambda x: x.sort_order)

    def get_list(self, list_id: str) -> TodoList | None:
        return next((lst for lst in self._lists if lst.id == list_id), None)

    def create_list(
        self, name: str, icon: str = "", description: str = "",
        color: str = "#94a3b8",
    ) -> TodoList:
        sort_order = max((lst.sort_order for lst in self._lists), default=-1) + 1
        lst = TodoList(
            name=name, icon=icon, description=description,
            color=color, sort_order=sort_order,
        )
        self._lists.append(lst)
        self._save()
        return lst

    # ------------------------------------------------------------------
    # Item CRUD
    # ------------------------------------------------------------------

    def add(self, **kwargs: Any) -> TodoItem:
        item = TodoItem(**kwargs)
        self._items.append(item)
        self._save()
        return item

    def get(self, item_id: str) -> TodoItem | None:
        return next((it for it in self._items if it.id == item_id), None)

    def list_items(
        self,
        status: str | None = None,
        list_id: str | None = None,
        idle_only: bool = False,
    ) -> list[TodoItem]:
        items = self._items
        if status:
            items = [i for i in items if i.status == status]
        if list_id:
            items = [i for i in items if i.list_id == list_id]
        if idle_only:
            items = [i for i in items if i.idle_eligible]
        return items

    def update(self, item_id: str, **kwargs: Any) -> TodoItem | None:
        item = self.get(item_id)
        if item is None:
            return None
        for k, v in kwargs.items():
            if hasattr(item, k) and k not in ("id", "created_at"):
                setattr(item, k, v)
        item.updated_at = time.time()
        if kwargs.get("status") == "done":
            item.completed_at = time.time()
        self._save()
        return item

    def remove(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [i for i in self._items if i.id != item_id]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def move(self, item_id: str, list_id: str) -> TodoItem | None:
        return self.update(item_id, list_id=list_id)

    # ------------------------------------------------------------------
    # Idle task selection
    # ------------------------------------------------------------------

    def next_idle_task(self) -> TodoItem | None:
        """Return the highest-priority pending idle-eligible task."""
        candidates = [
            i for i in self._items
            if i.idle_eligible and i.status in ("inbox", "queued", "in_progress")
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda i: (_PRIORITY_RANK.get(i.priority, 1), -i.created_at),
            reverse=True,
        )
        return candidates[0]
