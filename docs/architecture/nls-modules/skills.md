# Skills package (`nls/skills`)

**Skill SDK** for bundled and user-installed integrations: metadata, config schema, webhooks, Node bridges, channel adapters.

**Entry:** `nls/skills/__init__.py` — `SkillMeta`, `SkillContext`, `SkillBridge`, `SkillWebhook`, `SkillOnboarding`.

---

## Core modules

| File | Role |
|------|------|
| `agentskill_parser.py` | Parse `SKILL.md` AgentSkills format |
| `channel_processing.py` | `process_channel_message`, pending answer feed |

---

## Skill lifecycle

1. `server/services/skill_loader.py` discovers `nls/skills/bundled/` + `{data_dir}/skills/`
2. `register(app, ctx)` mounts FastAPI routes and webhooks
3. Optional `SkillBridge` spawns Node process (WhatsApp Baileys)
4. Per-agent enablement in `enabled_skills.json`

---

## Bundled skills

| Skill | Ingress | Notes |
|-------|---------|-------|
| `whatsapp-channel` | Webhook + Node bridge | Baileys `bridge/index.js` |
| `telegram-channel` | Webhook or poll | Bot API |
| `email-channel` | Resend via NestJS | `adapter.py` |
| `google-workspace` | OAuth | Gmail, Calendar, Drive, Sheets tools |
| `mcp-client` | User MCP servers | `MCPClientManager` |
| `todo-list` | REST + idle execution | Kanban / intentions |

Each exports `meta: SkillMeta` and `register(app, ctx)`.

---

## Disk per skill

Under skill directory:

- `config.json`, `agents/{agent_id}.json`, `data/`
- Bundled-specific stores (e.g. `todo-list/data/`)

---

## Related

- [Skills system](../skills-system.md)
- [Channels & webhooks](../channels-and-webhooks.md)
- [Extension: add bundled skill](../../extension/add-bundled-skill.md)
