# ClawHub dual proxy

ClawHub (`https://clawhub.ai`) is reached through **two** proxies depending on which tier handles the request.

---

## When to use which

| Client context | Proxy | Install location |
|----------------|-------|------------------|
| **Desktop runtime** (localhost:9222) | Python `/api/clawhub/*` | `{NLS_DATA_DIR}/skills/{slug}/` |
| **Hosted web** (logged-in user) | NestJS `/api/clawhub/*` | DB record + sync to desktop on relay |
| **Agent tool** `clawhub` | Python runtime URL | Same as desktop |

The Angular app calls NestJS in cloud mode and may call the local runtime in Electron mode.

---

## Python runtime

**Source:** `server/routes/skills.py` → `clawhub_router`

- Cached GETs (10 min TTL) for search/featured
- `POST /install` downloads zip or `SKILL.md` bundle directly into data dir
- No PostgreSQL — filesystem is source of truth

Agent tool: `nls/tools/agent_tools/clawhub.py` → `search`, `install`, `list`.

---

## NestJS backend

**Source:** `backend/src/clawhub/`

| Route | Purpose |
|-------|---------|
| GET `/api/clawhub/search` | Search (JWT) |
| GET `/api/clawhub/skill/:slug` | Metadata |
| POST `/api/clawhub/install` | Install to agent (ties to Prisma `clawhubSkill`) |
| GET `/api/clawhub/installed/:agentId` | List installed |
| DELETE `/api/clawhub/uninstall` | Remove |

Use NestJS when you need **account-level** install history or hosted UI without a local disk path.

---

## After install

1. Skill appears in Tools UI
2. Agent may need **`request_restart`** to reload skill registry
3. Heavy use → [crystallization](../guides/tools-and-skills.md)

---

## Related

- [ClawHub user guide](../guides/integrations/clawhub.md)
- [Skills admin API](../reference/skills-admin-api.md)
- [MCP & ClawHub extension](../extension/mcp-and-clawhub.md)
