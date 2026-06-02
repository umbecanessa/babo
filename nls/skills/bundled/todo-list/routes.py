"""REST API routes for the todo-list skill (consumed by the Angular frontend)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["todo-list"])


class ItemCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "normal"
    status: str = "inbox"
    list_id: str = "inbox"
    idle_eligible: bool = False
    source: str = "user"
    tags: list[str] = Field(default_factory=list)


class ItemUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    list_id: str | None = None
    idle_eligible: bool | None = None
    tags: list[str] | None = None
    notes: str | None = None
    due_date: str | None = None


class ListCreate(BaseModel):
    name: str
    icon: str = ""
    description: str = ""
    color: str = "#94a3b8"


def _get_manager(request: Request) -> Any:
    """Resolve the TodoManager from the skill loader."""
    sl = getattr(request.app.state, "skill_loader", None)
    if sl is None:
        raise HTTPException(503, "Skill loader not initialized")
    skill = sl.skills.get("todo-list")
    if skill is None:
        raise HTTPException(503, "todo-list skill not loaded")
    ctx = getattr(skill, "context", None)
    if ctx is None:
        raise HTTPException(503, "todo-list skill context not available")
    adapter = getattr(ctx, "adapter", None)
    if adapter is None:
        raise HTTPException(503, "todo-list manager not initialized")
    return adapter


# ------------------------------------------------------------------
# Lists
# ------------------------------------------------------------------

@router.get("/{agent_id}/lists")
async def get_lists(agent_id: str, request: Request) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    return {"lists": [lst.to_dict() for lst in store.get_lists()]}


@router.post("/{agent_id}/lists")
async def create_list(
    agent_id: str, body: ListCreate, request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    lst = store.create_list(
        name=body.name, icon=body.icon,
        description=body.description, color=body.color,
    )
    return {"list": lst.to_dict()}


# ------------------------------------------------------------------
# Items
# ------------------------------------------------------------------

@router.get("/{agent_id}/items")
async def get_items(
    agent_id: str,
    request: Request,
    status: str | None = None,
    list_id: str | None = None,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    items = store.list_items(status=status, list_id=list_id)
    return {"items": [it.to_dict() for it in items]}


@router.post("/{agent_id}/items")
async def create_item(
    agent_id: str, body: ItemCreate, request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    item = store.add(
        title=body.title,
        description=body.description,
        priority=body.priority,
        status=body.status,
        list_id=body.list_id,
        idle_eligible=body.idle_eligible,
        source=body.source,
        tags=body.tags,
    )
    if item.idle_eligible:
        mgr.sync_idle_intention(agent_id)
    await mgr.broadcast(agent_id, "added", item)
    return {"item": item.to_dict()}


@router.get("/{agent_id}/items/{item_id}")
async def get_item(
    agent_id: str, item_id: str, request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    item = store.get(item_id)
    if item is None:
        raise HTTPException(404, f"Item '{item_id}' not found")
    return {"item": item.to_dict()}


@router.put("/{agent_id}/items/{item_id}")
async def update_item(
    agent_id: str, item_id: str, body: ItemUpdate, request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    item = store.update(item_id, **updates)
    if item is None:
        raise HTTPException(404, f"Item '{item_id}' not found")
    if "idle_eligible" in updates or "status" in updates:
        mgr.sync_idle_intention(agent_id)
    action = "completed" if updates.get("status") == "done" else "updated"
    await mgr.broadcast(agent_id, action, item)
    return {"item": item.to_dict()}


@router.delete("/{agent_id}/items/{item_id}")
async def delete_item(
    agent_id: str, item_id: str, request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    item = store.get(item_id)
    if item is None or not store.remove(item_id):
        raise HTTPException(404, f"Item '{item_id}' not found")
    mgr.sync_idle_intention(agent_id)
    await mgr.broadcast(agent_id, "removed", item)
    return {"deleted": item_id}


# ------------------------------------------------------------------
# Plan linked to a todo item
# ------------------------------------------------------------------

@router.get("/{agent_id}/items/{item_id}/plan")
async def get_todo_plan(
    agent_id: str, item_id: str, request: Request,
) -> dict[str, Any]:
    """Return the plan linked to a todo item (steps with status)."""
    mgr = _get_manager(request)
    store = mgr.get_store(agent_id)
    item = store.get(item_id)
    if item is None:
        raise HTTPException(404, f"Item '{item_id}' not found")
    if not item.plan_id:
        return {"plan": None}

    # Resolve workspace from agent manager to read plan files
    am = getattr(request.app.state, "agent_manager", None)
    if am is None:
        raise HTTPException(503, "Agent manager not available")
    runtime = am.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(503, f"Runtime for agent '{agent_id}' not loaded")

    agent_dir = getattr(runtime, "agent_dir", None)
    workspace = str(agent_dir / "workspace") if agent_dir else ""
    if not workspace:
        return {"plan": None}

    try:
        from nls.agentic.plan_store import PlanStore
        plan_store = PlanStore(workspace)
        plan = plan_store.load(item.plan_id)
    except Exception:
        return {"plan": None}

    if plan is None:
        return {"plan": None}

    return {
        "plan": {
            "id": plan.id,
            "title": plan.title,
            "status": plan.status,
            "progress": plan.progress_summary(),
            "steps": [
                {
                    "id": s.id,
                    "label": s.label,
                    "status": s.status,
                    "notes": s.notes,
                }
                for s in plan.steps
            ],
        },
    }
