# Python HTTP & WebSocket API

FastAPI application: `server/main.py` → `create_app()`.

**Default port:** `NLS_SERVE_PORT` (9222 desktop, 8443 in `config.py` default).

**Auth:** `X-Runtime-Secret` (NestJS) or `Authorization: Bearer nlsk_...` (`server/middleware/auth.py`).

---

## Public / health

| Method | Path | Module | Purpose |
|--------|------|--------|---------|
| GET | `/health` | `routes/health.py` | Liveness, inference, sleep, consciousness flags |

---

## Agents

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agents/genesis` | List genesis templates |
| POST | `/agents` | Create agent from genesis |
| GET | `/agents` | List loaded agents |
| GET | `/agents/{id}` | Agent status snapshot |
| GET/PATCH | `/agents/{id}/job` | Owner Job charter (`routes/job_trust.py`) |
| GET/PATCH | `/agents/{id}/trust` | Owner Trust rails (`routes/job_trust.py`) |
| DELETE | `/agents/{id}` | Unload agent |
| PATCH | `/agents/{id}/name` | Rename |
| PATCH | `/agents/{id}/owner-email` | Owner identity for channels |
| GET/PATCH | `/agents/{id}/inference` | Per-agent model config: `orchestrator_model`, `orchestrator_route` (`local`\|`cloud`), `delegate_model`, `delegate_route`, `delegate_lock_orchestrator`; `clear_orchestrator` / `clear_delegate` on PATCH |
| GET | `/agents/{id}/relay-status` | NestJS relay connection state |
| GET/DELETE | `/agents/{id}/processes` | Background project processes |
| POST | `/agents/{id}/pause` / `unpause` / `evict` | Lifecycle |
| GET | `/agents/{id}/working-memory` | WM slots |
| GET | `/agents/{id}/theory-of-mind` | ToM model |
| GET | `/agents/{id}/narrative/episodes` | Episodes |
| GET | `/agents/{id}/network-dynamics` | Network metrics |

---

## Chat

| Type | Path | Purpose |
|------|------|---------|
| **WebSocket** | `/ws/chat/{agent_id}` | Streaming chat + agentic events |
| POST | `/chat/relay` | Chat forwarded from NestJS relay (`routes/chat/endpoints.py`) |
| GET | `/sessions/{agent_id}` | List chat sessions |
| GET | `/sessions/{agent_id}/{session_key}` | Session history |

WebSocket handler: `server/routes/chat/ws_handler.py`.

---

## OpenAI-compatible

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/chat/completions` | Per-agent completions (`routes/completions.py`) |
| GET | `/v1/models` | Model list |

---

## Admin (runtime inspection)

Prefix `/admin/` — see **[Admin API](admin-api.md)** and **[Skills admin API](skills-admin-api.md)** for full route tables.

---

## Skills (per agent + global)

| Prefix | Purpose |
|--------|---------|
| `/admin/skills/` | Global skill registry (`routes/skills.py`) |
| `/admin/agents/{id}/skills/` | Per-agent enable / disable |
| `/admin/skills/crystallization/` | Crystallization candidates |
| `/api/clawhub/` | ClawHub search, install proxy |
| `/skills/{channel}-channel/webhook/{id}` | Channel skill webhooks (mounted by SkillLoader) |

Full route tables: **[Skills admin API](skills-admin-api.md)**.

---

## Files & IDE

| Prefix | Purpose |
|--------|---------|
| `/fs/*` | IDE filesystem (`routes/filesystem.py`) |
| `/agents/{id}/files/*` | Workspace upload/download |
| WS | `/ws/terminal` | User shell (xterm) — `routes/terminal_ws.py` |

---

## Channels & webhooks

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/webhooks/telegram/{id}` | Telegram ingress (`routes/webhooks.py`) |
| POST | `/webhooks/generic/{id}` | Generic webhook ingress |
| GET | `/webhooks/status` | Webhook subsystem status |
| POST | `/skills/*-channel/webhook/{id}` | Skill bridges (WhatsApp, Telegram, Discord, Slack, …) |
| GET | `/skills/discord-channel/channels/{id}` | List/sync Discord scoped channels |
| POST | `/skills/discord-channel/channels/{id}/sync` | Refresh Discord channel list |
| PATCH | `/skills/discord-channel/channels/{id}/{channelId}` | Update Discord scope |
| GET | `/skills/slack-channel/channels/{id}` | List/sync Slack scoped channels |
| POST | `/skills/slack-channel/channels/{id}/sync` | Refresh Slack channel list |
| PATCH | `/skills/slack-channel/channels/{id}/{channelId}` | Update Slack scope |
| GET | `/skills/discord-channel/status/{id}` | Discord connection status |
| GET | `/skills/slack-channel/status/{id}` | Slack connection status |
| GET | `/channels/{id}/status` | Channel status (`routes/channels.py`) |
| GET | `/channels/{id}/threads` | Channel thread list |

Production cloud path uses NestJS → relay — [Channels & webhooks](../architecture/channels-and-webhooks.md).

---

## Transcribe

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/transcribe` | Speech-to-text (local Whisper or GPU worker proxy) |

See [Transcribe & GPU worker](../configuration/transcribe-and-gpu-worker.md).

---

## Chat commands

WebSocket slash commands: [Chat slash commands](chat-commands.md).

---

## Teams & orchestration

| Prefix | Purpose |
|--------|---------|
| `/api/agents/{id}/teams/*` | See **[Teams & projects API](teams-api.md)** |

---

## Squads (persistent fleet)

| Prefix | Purpose |
|--------|---------|
| `/api/squads` | Squad CRUD, kanban, by-agent lookup |

Full tables: **[Job, Trust & Squad API](job-trust-squad-api.md)**. No NestJS duplicate routes — hosted web uses `/api/rt` proxy only.

---

## Startup side effects

On boot (`server/main.py` lifespan):

1. Seed genesis templates
2. Load inference client + tokenizer
3. Start sleep scheduler
4. `SkillLoader.load_all()` + startup hooks (Node bridges, pollers)
5. `auto_load_all()` agents from disk
6. Optional `ChannelRelayClient` per agent if `NESTJS_URL` set
7. Start consciousness scheduler + inner loops

---

## Related

- [Agent runtime](../architecture/agent-runtime.md)
- [Relay protocol](relay-protocol.md)
- [Server architecture](../architecture/server.md)
