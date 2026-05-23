# Deployment topologies

Babo is not “one binary in the cloud.” It is a **split stack**: a hosted **control plane** (auth, registry, relay) and a **local brain** (Python runtime + agent memory on your machine).

This page is the map of **who talks to whom**.

---

## The three runtime surfaces

| Surface | What it is | Where it runs |
|---------|------------|---------------|
| **Angular UI** | Chat, projects, memory, tools | Electron window **or** static web app (CDN / Railway / nginx) |
| **NestJS backend** | Accounts, agents in Postgres, WebSocket chat, channel webhooks, relay | Your cloud (e.g. Railway) |
| **Python runtime** | Agentic loop, Cryptex, skills, sleep, tools | **Desktop machine** (`127.0.0.1:9222`) |

Inference (OpenRouter, Ollama, etc.) is a **fourth** external HTTP API the Python runtime calls.

---

## Primary product model: desktop hub + remote web

This is the intended public setup after the pivot away from “run the whole brain in the cloud.”

```text
┌─────────────────────────────────────────────────────────────────┐
│  Browser (hosted Angular)          Electron (optional local UI) │
│  isRemote = true                   isElectron = true            │
│  apiUrl → NestJS                   apiUrl → NestJS (auth)     │
│  runtimeUrl → /api/rt (proxy)      runtimeUrl → 127.0.0.1:9222 │
└───────────────┬──────────────────────────────┬──────────────────┘
                │ Socket.IO /chat              │ WebSocket direct
                ▼                              ▼
┌─────────────────────────── NestJS backend ───────────────────────┐
│  JWT auth · Postgres agents · /api/rt HTTP proxy                 │
│  /api/channels/relay/{runtimeAgentId}  ← outbound WS from desktop│
└───────────────────────────────┬──────────────────────────────────┘
                                │ chat_request · http_proxy · webhooks
                                ▼
┌────────────────── Desktop Python runtime (:9222) ──────────────┐
│  AgentRuntime · data/agents/ · skills · sleep                    │
│  ChannelRelayClient → connects **outbound** to NestJS relay WS   │
└───────────────────────────────┬──────────────────────────────────┘
                                ▼
                         Inference API (BYO)
```

### Why the relay exists

The desktop runtime is usually **behind NAT**. NestJS cannot open HTTP to your laptop. Instead:

1. On startup (when `NESTJS_URL` is set), each loaded agent starts a **`ChannelRelayClient`** (`nls/runtime/channels.py`).
2. It opens a WebSocket **to** `wss://<nestjs>/api/channels/relay/{runtimeAgentId}?secret=...`.
3. NestJS registers that socket in `ChannelsService.relaySockets`.

Remote web chat then flows:

```text
Browser → Socket.IO join(agentId)
       → NestJS sees hasRelaySocket(runtimeAgentId)
       → relayMode = true
       → pushChatToRelay → desktop processes chat → chat_response back
```

HTTP APIs from the browser use the same tunnel:

```text
Browser → GET/POST /api/rt/...  (JwtAuthGuard)
       → RuntimeProxyController
       → proxyHttpViaRelay(method, path, body)
       → desktop runtime handles locally
```

### How to tell which path is active

| Check | Desktop UI | Remote web |
|-------|------------|------------|
| Platform | `PlatformService.isElectron` | `isRemote` |
| Chat transport | Raw WebSocket to `127.0.0.1:9222` | Socket.IO `/chat` on NestJS |
| Agent “online” on dashboard | Always local | `GET /api/agents/:id/relay-status` |
| Error if hub offline | N/A | “Agent desktop is not connected” |

---

## Desktop app (local-first UI)

**Source:** `desktop/electron/`, `frontend` with `environment.electron.ts`.

- Spawns **uvicorn** on `NLS_HOST` / `NLS_PORT` (default `127.0.0.1:9222`).
- Sets `NESTJS_URL` from the setup wizard so the relay starts automatically.
- Angular inside Electron talks **directly** to the runtime WebSocket for lowest latency.
- Still uses NestJS for login, agent registry, and cloud features.

Memory and workspace files live under the desktop user data directory (or repo `data/` in dev).

---

## Hosted web UI (remote dashboard)

**Build:** `ng build --configuration=production` (see `environment.prod.ts`).

| Setting | Value | Meaning |
|---------|-------|---------|
| `apiUrl` | `/api` | Same origin as NestJS or reverse-proxied |
| `runtimeUrl` | `/api/rt` | Never hits Python directly from the browser |
| `wsUrl` | `''` or Nest origin | Socket.IO chat namespace |

The web UI is **not a second brain**. It is a **remote control panel** for agents whose compute and memory live on a machine running the desktop runtime (or, in advanced self-host, a reachable Python server).

---

## Secondary model: co-located self-host (direct `RUNTIME_URL`)

If you run Python on a server NestJS can reach (same VPC, public IP + secret), chat may use **direct** mode:

- `ChannelsService.hasRelaySocket(agentId)` is false
- `ChatGateway` calls `RuntimeService.connectChat()` → WebSocket/HTTP to `RUNTIME_URL`

Use this for lab servers or a single-machine Docker stack. It is **not** the desktop-hub product path, but the code path remains supported.

Set on NestJS:

```env
RUNTIME_URL=http://your-python-host:9222
RUNTIME_SHARED_SECRET=...
```

On Python:

```env
NLS_SHARED_SECRET=<same>
NLS_SERVE_HOST=0.0.0.0
```

---

## Channel integrations (Telegram, WhatsApp, …)

Inbound webhooks hit **NestJS** (`POST /api/channels/webhook/:channel/:agentId`).

Delivery order:

1. **Push** on the relay WebSocket if desktop is connected (`channel_message`).
2. Else **queue** for later drain when relay reconnects.
3. Desktop routes locally to `http://127.0.0.1:9222/skills/.../webhook/...`.

So channels work when the desktop hub is online, even if the user only uses the web UI.

---

## Data placement

| Data | Location |
|------|----------|
| User accounts, agent rows | PostgreSQL (NestJS) |
| `runtimeAgentId` mapping | Postgres `agents.runtimeAgentId` |
| Memory, Cryptex, plans, skills | `data/agents/{runtimeAgentId}/` on desktop disk |
| Inference traffic | Your provider; not stored by Babo |

---

## Recommended cloud layout (Railway / similar)

| Service | Deploy | Notes |
|---------|--------|-------|
| **Postgres** | Railway plugin | `DATABASE_URL` |
| **NestJS** | Railway Node service | `prisma migrate deploy`, `RUNTIME_*` only if using direct mode |
| **Angular** | Railway static or CDN | Proxy `/api` → NestJS |
| **Python runtime** | **Not** on Railway for desktop-hub mode | Users run desktop locally |

See [Cloud deployment](../configuration/cloud-deployment.md).

---

## Related

- [Desktop app architecture](desktop.md)
- [Server runtime](server.md)
- [Data flow](data-flow.md)
- [Inference & plugins](inference-and-plugins.md)
