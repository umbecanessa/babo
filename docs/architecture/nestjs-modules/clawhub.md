# ClawHub module

**Path:** `backend/src/clawhub/`

Search and install skills from **ClawHub** (`https://clawhub.ai/api/v1`), track installs in Postgres, push bundles to desktop via relay.

---

## Files

| File | Role |
|------|------|
| `clawhub.module.ts` | Imports `ChannelsModule` |
| `clawhub.controller.ts` | JWT routes |
| `clawhub.service.ts` | External API + `pushSkillInstall` |

---

## HTTP routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/clawhub/search` | `q`, `limit` |
| GET | `/api/clawhub/skill/:slug` | Metadata |
| POST | `/api/clawhub/install` | Body: `slug`, `agentId`, optional `version` |
| GET | `/api/clawhub/installed` | Query `agentId?` |
| DELETE | `/api/clawhub/uninstall/:slug` | Query `agentId?` |

---

## Install flow

1. Download bundle from ClawHub API
2. Upsert `ClawhubSkill` in Prisma
3. `ChannelsService.pushSkillInstall` → relay message `skill_install`
4. Desktop Python extracts under `{data_dir}/skills/{slug}/`

Parallel Python proxy: [ClawHub dual proxy](../clawhub-proxy.md).

---

## Prisma

- `ClawhubSkill` — unique `(slug, agentId)`

---

## Python runtime

No direct HTTP from Nest to Python for install — relay delivers files. Agent tool `clawhub` uses local `/api/clawhub` on desktop.

---

## Related

- [Skills admin API](../../reference/skills-admin-api.md)
- [ClawHub integration guide](../../guides/integrations/clawhub.md)
