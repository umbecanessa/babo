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
| `main.ts` | Window, lifecycle |
| `ipc-handlers.ts` | IPC registration (`setup:*`, `capabilities:*`, `runtime:*`, …) |
| `config-manager.ts` | Inference URL, model, API key, NestJS URL, `capabilityProfile` → env vars |
| `venv-manager.ts` | First-run venv + bundled standalone runtimes (Python, Node, PS7) |
| `runtime-manager.ts` | Start/stop uvicorn child process |
| `capability-scanner.ts` | Device GPU/RAM probe + LAN inference/vision/voice discovery |
| `capability-recommender.ts` | Recommend `capabilityProfile` tiers from scan |
| `capability-types.ts` | Profile + scan TypeScript types |
| `update-manager.ts` | GitHub releases auto-update |
| `permission-manager.ts` | User approval for sensitive skill actions |

---

## First-run wizard

The Angular **Setup** component (`frontend/…/setup/`) drives IPC:

1. `setup:check` / `setup:start` → progress `setup:progress`, logs `setup:log`
2. `capabilities:scan-device`, optional `capabilities:probe-lan`, `capabilities:recommend`
3. Four capability cards → `capabilities:test-inference`, `capabilities:apply-profile`
4. Account + backend URL → `config:set` (`nestjsUrl`, inference fields)
5. `runtime:start` → local server on port **9222**; `setupComplete` persisted

See [Desktop configuration](../configuration/desktop.md) for the full wizard flow and IPC table.

---

## Bundled runtimes (first-run)

`VenvManager.setup()` downloads standalone **Python 3.12**, **Node.js 20**, on Windows **PowerShell 7**, and **llmfit** (pinned GitHub release into `userData/llmfit-standalone`) into Electron userData, then creates the agent venv and installs `requirements-desktop.txt`. On each app start after setup, `checkDepsSync()` re-runs `pip install` when `requirements-desktop.txt` changes and re-checks Node, PowerShell, and llmfit versions. Runtime start injects `NLS_NODE_BIN`, `NLS_NPM_BIN`, and `NLS_PWSH_BIN` so skills and `bash()` work offline. Model Fit uses the bundled llmfit binary when present.

On Windows, agent `bash()` runs PowerShell 7 — see [Platform shell on Windows](platform-shell-and-windows.md).

---

## Environment mapping

Desktop writes settings to env when spawning Python:

| UI setting | Environment variable |
|------------|---------------------|
| Inference URL | `NLS_VLLM_BASE_URL` |
| Model | `NLS_HF_MODEL` |
| API key | `NLS_INFERENCE_API_KEY` (JWT or `nlsk_` when using Babo Cloud relay) |
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
- [Platform shell on Windows](platform-shell-and-windows.md)
- [IPC reference](../desktop/ipc-reference.md)
