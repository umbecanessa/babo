# Skills admin API

Skill registry, ClawHub proxy, reviews, and per-agent enablement.

**Source:** `server/routes/skills.py`

---

## Global registry (`/admin/skills`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/skills` | List installed skills |
| GET | `/admin/skills/{name}` | Skill metadata |
| POST | `/admin/skills/{name}/enable` | Enable globally |
| POST | `/admin/skills/{name}/disable` | Disable globally |
| DELETE | `/admin/skills/{name}` | Uninstall skill |
| GET | `/admin/skills/{name}/onboarding` | Onboarding wizard state |
| GET | `/admin/skills/{name}/config/schema` | Config JSON schema |
| GET | `/admin/skills/{name}/config` | Current config |
| PATCH | `/admin/skills/{name}/config` | Update config |
| GET | `/admin/skills/{name}/files/{path}` | Read skill file |
| PUT | `/admin/skills/{name}/files/{path}` | Write skill file |
| GET | `/admin/skills/{name}/brain` | Myelination / crystallization stats |
| POST | `/admin/skills/{name}/repair` | SSE repair stream |

---

## Reviews queue

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/skills/reviews/list` | Pending reviews |
| GET | `/admin/skills/reviews/{id}` | Review detail |
| POST | `/admin/skills/reviews/{id}/approve` | Approve install |
| POST | `/admin/skills/reviews/{id}/reject` | Reject install |

---

## Per-agent skills (`/admin/agents`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/agents/{id}/skills` | List skills + `enabled_for_agent` |
| POST | `/admin/agents/{id}/skills/{name}/enable` | Enable for agent |
| POST | `/admin/agents/{id}/skills/{name}/disable` | Disable for agent |

---

## ClawHub proxy (Python runtime)

Prefix: **`/api/clawhub`** (`clawhub_router` in same file)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/clawhub/search` | Vector search (`q`, `limit`) |
| GET | `/api/clawhub/featured` | Featured list (`sort`, `limit`) |
| GET | `/api/clawhub/skill/{slug}` | Resolve skill metadata |
| POST | `/api/clawhub/install` | Download + extract to `{data_dir}/skills/{slug}` |

Used by: Angular UI when pointed at desktop runtime, and the **`clawhub`** agent tool (`nls/tools/agent_tools/clawhub.py`).

NestJS also exposes **`/api/clawhub/*`** with PostgreSQL install tracking — see [ClawHub dual proxy](../architecture/clawhub-proxy.md).

---

## Channel webhooks (dynamic)

Skill loader mounts per skill, e.g.:

```http
POST /skills/whatsapp-channel/webhook/{agent_id}
POST /skills/telegram-channel/webhook/{agent_id}
POST /skills/discord-channel/webhook/{agent_id}
POST /skills/slack-channel/webhook/{agent_id}
```

**Channel scope** (Discord / Slack):

```http
GET    /skills/discord-channel/channels/{agent_id}
POST   /skills/discord-channel/channels/{agent_id}/sync
PATCH  /skills/discord-channel/channels/{agent_id}/{channel_id}

GET    /skills/slack-channel/channels/{agent_id}
POST   /skills/slack-channel/channels/{agent_id}/sync
PATCH  /skills/slack-channel/channels/{agent_id}/{channel_id}
```

Not under `/admin/` — see [Channels & webhooks](../architecture/channels-and-webhooks.md).

---

## Crystallization

Router prefix for skill crystallization jobs — see `crystallization_router` in `skills.py` and [Tools & skills](../guides/tools-and-skills.md).

---

## Related

- [Admin API](admin-api.md)
- [ClawHub proxy](../architecture/clawhub-proxy.md)
- [Skills system](../architecture/skills-system.md)
