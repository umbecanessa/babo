# Add a bundled skill

Skills package tools, HTTP routes, bridges, and onboarding.

**Ship path:** `nls/skills/bundled/{skill-name}/`  
**User override:** `data/skills/{skill-name}/`

---

## Step 1: Scaffold

```text
nls/skills/bundled/my-skill/
├── __init__.py       # meta + register()
├── SKILL.md          # optional AgentSkill instructions
├── config.schema.json  # optional UI form
└── adapter.py        # optional channel logic
```

---

## Step 2: Define meta

```python
# __init__.py
from nls.skills import SkillMeta, SkillContext

meta = SkillMeta(
    name="my-skill",
    version="1.0.0",
    description="Short description for Tools page",
    author="you",
)

def register(app, ctx: SkillContext):
    ...
```

---

## Step 3: Register capabilities

```python
def register(app, ctx: SkillContext):
    from .tool import MySkillTool

    ctx.register_tool_factory(lambda agent_id: MySkillTool(agent_id))

  # Optional FastAPI routes
    from .routes import router
    ctx.include_router(router, prefix="/skills/my-skill")

  # Optional startup (Node, pollers)
    ctx.on_startup(start_my_bridge)
```

Routers mount on the **Python** FastAPI app — channel webhooks still enter via NestJS relay.

---

## Step 4: AgentSkill instructions (optional)

`SKILL.md`:

```markdown
---
name: my-skill
description: When to use this skill
---

# My Skill

Instructions appended to system prompt when enabled.
```

---

## Step 5: Config schema (optional)

`config.schema.json` drives the Tools page form. Persisted per agent under `data/agents/{id}/skills/`.

---

## Step 6: Discovery

`SkillLoader.load_all()` scans bundled + data dirs on server start.

No manual import list — directory name = skill name.

---

## Step 7: Enable per agent

Tools UI → enable skill → `setup_tools` injects factories + instructions.

Optional onboarding: `server/routes/skills.py` onboarding endpoints.

---

## Patterns to copy

| Pattern | Example skill |
|---------|---------------|
| Channel + webhook | `telegram-channel` |
| Node sidecar | `whatsapp-channel` |
| OAuth UI | `google-workspace` |
| MCP proxy | `mcp-client` |
| REST + WM bridge | `todo-list` |

---

## Related

- [Skills system](../architecture/skills-system.md)
- [Add a channel](add-channel-integration.md)
