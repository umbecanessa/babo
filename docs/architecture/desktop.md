# Desktop app architecture

The Babo **desktop app** wraps the Angular UI and a **local Python runtime** in Electron.

Source: `desktop/electron/`

---

## Layer diagram

```text
┌─────────────────────────────────────────────────┐
│  Electron                                        │
│  ┌───────────────┐    ┌──────────────────────┐  │
│  │ Renderer      │    │ Main process         │  │
│  │ (Angular UI)  │◄──►│ ConfigManager        │  │
│  │               │ IPC│ VenvManager          │  │
│  │ Chat·Projects │    │ RuntimeManager     │  │
│  │ Memory·Tools  │    │ UpdateManager      │  │
│  └───────┬───────┘    └──────────┬───────────┘  │
│          │ HTTP/WS               │ spawn        │
│  ┌───────▼───────────────────────▼───────────┐  │
│  │  Local Python runtime (:9222)               │  │
│  │  uvicorn server.main:app                    │  │
│  └───────────────────┬───────────────────────┘  │
└──────────────────────┼──────────────────────────┘
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
  NestJS backend              Inference API
  (auth, relay)            (OpenRouter, Ollama, …)
```

---

## Main process modules

| Module | Role |
|--------|------|
| `main.ts` | Window, lifecycle, IPC registration |
| `config-manager.ts` | Inference URL, model, API key, NestJS URL → env vars |
| `venv-manager.ts` | First-run Python venv + pip install |
| `runtime-manager.ts` | Start/stop uvicorn child process |
| `update-manager.ts` | GitHub releases auto-update |
| `permission-manager.ts` | User approval for sensitive skill actions |

---

## First-run wizard

The Angular **Setup** component (`frontend/…/setup/`) drives IPC:

1. `venv:start-setup` → progress events → `venv:setup-progress`
2. User enters inference + backend URLs
3. `runtime:start` → local server on port **9222**
4. Config persisted for subsequent launches

See [Desktop configuration](../configuration/desktop.md).

---

## Environment mapping

Desktop writes settings to env when spawning Python:

| UI setting | Environment variable |
|------------|---------------------|
| Inference URL | `NLS_VLLM_BASE_URL` |
| Model | `NLS_HF_MODEL` |
| API key | `NLS_INFERENCE_API_KEY` |
| (implicit) | `NLS_PRODUCT_MODE=1` |

---

## Outbound relay to NestJS

When `NESTJS_URL` is set (from the setup wizard’s backend URL), the Python runtime starts **`ChannelRelayClient`** per agent (`nls/runtime/channels.py`):

```text
Desktop Python  --WebSocket outbound-->  wss://<nestjs>/api/channels/relay/{runtimeAgentId}
```

NestJS uses this socket to:

- Forward **web chat** (`chat_request` / `chat_response`)
- **Proxy HTTP** (`http_proxy`) for `/api/rt/*`
- Push **channel webhooks** (Telegram, WhatsApp, …) to local skill adapters
- **Broadcast** runtime events to remote browsers

The desktop does not expose port 9222 to the public internet.

---

## Frontend routing in Electron

`environment.electron.ts`:

| Setting | Value |
|---------|-------|
| `runtimeUrl` | `http://127.0.0.1:9222` |
| `wsUrl` | `ws://127.0.0.1:9222` |
| `apiUrl` | NestJS (auth, registry) |

`WebSocketService.useRawWs = true` — chat goes **directly** to the local runtime, not through Socket.IO relay.

---

## Hosted web vs Electron

| | Electron | Hosted web |
|---|----------|------------|
| UI shell | Desktop window | Browser tab |
| Chat WS | Direct to `:9222` | NestJS → relay → desktop |
| Memory | Local `data/` | Same (on desktop disk) |
| Requires desktop running | For local UI only | **Yes**, for agent execution |

See [Deployment topologies](deployment-topologies.md).

---

## Security model

- Runtime binds to **localhost** by default
- Agent data stays in local `data/agents/`
- Inference API key stored in OS user config (not in repo)
- Auto-update pulls signed releases from GitHub

---

## Build pipeline

```bash
cd desktop
npm run build      # compile TS + bundle Angular into electron
npm run dist:win   # NSIS installer (Windows)
npm run dist:mac   # DMG (macOS)
```

Output: `desktop/release/` or electron-builder target dir.

---

## Related

- [Installation](../getting-started/installation.md)
- [Server runtime](server.md)
- [Configuration — desktop](../configuration/desktop.md)
