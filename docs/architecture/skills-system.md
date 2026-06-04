# Skills system

Skills extend agents with **tools**, **HTTP routers**, **channel bridges**, and **startup hooks**.

**Loader:** `server/services/skill_loader.py`  
**SDK:** `nls/skills/__init__.py` (`SkillMeta`, `SkillContext`)

---

## Skill formats

| Format | Files | Behavior |
|--------|-------|----------|
| **Native** | `__init__.py` with `meta` + `register(app, ctx)` | Full Python — routers, bridges, factories |
| **AgentSkill** | `SKILL.md` | YAML frontmatter + markdown → system prompt |
| **Instruction-only** | `SKILL.md`, no `config_schema` | ClawHub/AgentSkill — setup via read + `bash()` / Python; **not** `skill_configure` |
| **Hybrid** | Both | Native tools + extra instructions |

---

## Discovery paths

| Path | Precedence |
|------|------------|
| `data/skills/{name}/` | Highest (user/ClawHub) |
| `nls/skills/bundled/{name}/` | Shipped defaults |

---

## Bundled skills (shipped)

| Skill | Directory | Role |
|-------|-----------|------|
| todo-list | `bundled/todo-list/` | Kanban API, idle intentions |
| mcp-client | `bundled/mcp-client/` | MCP connect, dynamic tools |
| google-workspace | `bundled/google-workspace/` | OAuth Gmail/Calendar/Drive/Sheets |
| email-channel | `bundled/email-channel/` | Resend inbox |
| telegram-channel | `bundled/telegram-channel/` | Bot + webhook relay |
| discord-channel | `bundled/discord-channel/` | NestJS Gateway + scoped guild channels |
| slack-channel | `bundled/slack-channel/` | Events API + scoped workspace channels |
| whatsapp-channel | `bundled/whatsapp-channel/` | Baileys Node bridge |

Discord and Slack share **`nls/skills/channel_scope.py`** for two-way channel sync and compile `PolicyEnforcer` groups from effective scoped channels.

---

## Native skill contract

```python
# nls/skills/bundled/example/__init__.py
from nls.skills import SkillMeta, SkillContext

meta = SkillMeta(name="example", version="1.0.0", ...)

def register(app, ctx: SkillContext):
    ctx.register_tool_factory(lambda agent_id: MyTool(agent_id))
    ctx.include_router(router, prefix="/skills/example")
    ctx.on_startup(my_startup)
    ctx.register_bridge("example", MyBridge())
```

### SkillContext API

| Method | Purpose |
|--------|---------|
| `register_tool(tool)` | Static tool all agents |
| `register_tool_factory(fn)` | Per-agent tool instance |
| `include_router(router, prefix)` | FastAPI routes on main app |
| `on_startup` / `on_shutdown` | Lifecycle |
| `register_bridge(name, bridge)` | Channel bridge instance |
| `register_poller` / `register_schedule` | Scheduler integration |

---

## Startup sequence

```text
server/main.py lifespan
  → SkillLoader.load_all()
  → mount routers on FastAPI app
  → run_startup_hooks()
        ├── Start Node sidecars (WhatsApp)
        ├── Register pollers with SchedulerManager
        └── Health checks
```

---

## Per-agent enablement

`AgentRuntime._get_enabled_skills()` reads agent config.

`nls/tools/tool_setup._inject_skill_tools()`:

- `skill_loader.tools_for(enabled)`
- `skill_loader.tool_factories_for(enabled)`
- Instructions appended via `skill_loader.instructions_for()`

Tools page: enable/disable, schema forms, onboarding.

**Instruction-only setup policy:** `nls/skills_setup_policy.py` — activation checklist, Windows `.sh` guidance, Python-first nudge for API skills. See [Platform shell on Windows](platform-shell-and-windows.md).

---

## ClawHub installs

NestJS `clawhub` module downloads skill → DB → `pushSkillInstall` over relay → files written to `data/skills/`.

---

## Channel skills & relay

| Skill | NestJS ingress |
|-------|----------------|
| Telegram | `POST /api/channels/webhook/telegram/{runtimeAgentId}` |
| Slack | `POST /api/channels/webhook/slack/{runtimeAgentId}` (+ signing secret register) |
| Discord | `POST /api/channels/discord/register/{runtimeAgentId}` (Gateway, not HTTP webhook) |
| Email | Resend → `/api/channels/email/webhook` |

NestJS forwards to desktop via relay — see [Channels](channels-and-webhooks.md).

---

## Extension

- [Add a bundled skill](../extension/add-bundled-skill.md)
- [Add a channel integration](../extension/add-channel-integration.md)
- [MCP & ClawHub](../extension/mcp-and-clawhub.md)

---

## Related

- [Tools system](tools-system.md)
- [Tools & skills guide](../guides/tools-and-skills.md)
