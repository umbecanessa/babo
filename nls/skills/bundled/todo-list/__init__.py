"""Todo List -- Bundled NLS skill for persistent task management.

Provides:
  - A ``todo`` agent tool for adding, listing, completing, and managing tasks
  - Multi-list support (Inbox, Projects, Research, Creative, custom)
  - Persistent per-agent JSON storage
  - WM intention bridge for idle-mode task execution via DMN autonomous dreams
  - REST API for the Angular Kanban board frontend
  - Real-time WebSocket broadcasting on task changes
"""

from nls.skills import SkillMeta

meta = SkillMeta(
    name="todo-list",
    version="1.0",
    description=(
        "Persistent todo list with multi-list Kanban board and "
        "idle-mode task execution"
    ),
)


def register(app, ctx):
    from .tool import TodoManager
    from .routes import router

    manager = TodoManager(app=app, ctx=ctx)
    ctx.adapter = manager

    ctx.include_router(router, prefix="/skills/todo-list")
    ctx.register_tool_factory(manager.create_tool)
    ctx.on_startup(manager.startup)
