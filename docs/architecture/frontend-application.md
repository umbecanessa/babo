# Frontend application

Angular SPA shared by **hosted web** and **Electron desktop**.

**Root:** `frontend/src/app/`

---

## Platform modes

`PlatformService` (`core/services/platform.service.ts`):

| Flag | When true | Implications |
|------|-----------|--------------|
| `isElectron` | `window.nls.isDesktop` or `environment.electron` | Direct runtime URLs |
| `isRemote` | `!isElectron` | All agent I/O via NestJS relay |

### Environment files

| File | Build | `apiUrl` | `runtimeUrl` | `wsUrl` |
|------|-------|----------|--------------|---------|
| `environment.ts` | Dev web | `localhost:3000/api` | `/api/rt` proxy | `localhost:3000` |
| `environment.prod.ts` | Hosted web | `/api` | `/api/rt` | same-origin |
| `environment.electron.ts` | Desktop | NestJS auth | `127.0.0.1:9222` | `ws://127.0.0.1:9222` |

Electron may override via IPC `urls:get` after config wizard.

---

## Routing

`app.routes.ts` (guards: `authGuard`, `setupGuard`):

| Route | Feature |
|-------|---------|
| `/setup` | Desktop first-run |
| `/auth/*` | Login/register |
| `/dashboard` | Agent list, relay status, **Squads** panel (fleet + charter modal) |
| `/create` | Genesis wizard |
| `/chat/:agentId` | Main chat + **run panel** + **model picker** |
| `/tools/:agentId` | Integrations & skills |
| `/projects/:agentId` | Board, timeline, teams |
| `/memory/:agentId` | Memory browser |
| `/brain/:agentId` | Hormones & drives |
| `/settings` | User settings |
| `/settings/api-keys` | Programmatic runtime keys |
| `/tasks/:agentId` | Alias → same as `/projects/:agentId` |

---

## Core services

| Service | Role |
|---------|------|
| `ApiService` | HTTP — splits NestJS vs runtime URLs |
| `WebSocketService` | Chat — **one Socket.IO/WS connection per agent** (parallel runs) |
| `AgentModelService` | Per-agent session + delegate model binding |
| `RunViewService` | Run panel timeline state |
| `ThemeService` | Light/dark theme tokens |
| `WorkspaceNavService` | Project workspace routing |
| `AuthService` | JWT storage |
| `BaboCloudProvisionService` | Sync JWT/`nlsk_` → runtime `NLS_INFERENCE_API_KEY` (Babo Cloud) |
| `TerminalService` | User shell via `/terminal` |
| `FilesystemService` | IDE — IPC in Electron, API in web |
| `ChatWorkbenchService` | Workbench panels |
| `ChatMainTranscriptService` | Shared Home-thread transcript between Chat and Projects |
| `AgentOrchestrationProfileService` | Per-agent orchestration profile + active-plan floor |
| `UpdateService` | Desktop auto-update |

### ApiService routing logic

- Auth, agents list, relay-status → `environment.apiUrl`
- Agent brain/memory/status in Electron → `runtimeUrl` direct
- Agent ops in web → `runtimeUrl` = `/api/rt` (proxied)

---

## Chat component

`features/chat/chat.component.ts`:

**Remote mode startup:**

```typescript
if (platform.isRemote) {
  api.getRelayStatus(agentId) → agentOnline
  if online → ws.connect() + joinAgent()
}
```

Shows offline banner when desktop disconnected.

**Message handling:** `handleRuntimeMessage` maps `runtime` events to UI (thoughts, tools, signals).

---

## Dashboard relay UX

`dashboard.component.ts` loads `getAllRelayStatus()` in remote mode.

Agent cards show **Desktop Offline** when relay down.

---

## Projects workspace

`features/projects/` — board, **overview strip**, **workspace IDE** (CodeMirror), teams.

Uses `ProjectService` + runtime APIs for plan/team state. Legacy IDE/files/timeline panels replaced by unified **workspace** component.

---

## Chat workbench (2026)

| Component | Path | Role |
|-----------|------|------|
| Run panel | `run-panel/` | Live tool calls, orchestration events |
| Model picker | `chat-model-picker/` | Session model + delegate override |
| Workbench utils | `workbench-display.util.ts` | Density, labels, activity formatting |

**Multi-agent:** opening several agents keeps separate WebSocket sessions so parallel benchmark runs do not cross-stream events.

**Transcript sync (v1.1.12+):** Home chat history is shared between `/chat/:agentId` and the Projects chat sidebar via `ChatMainTranscriptService`. Agentic tool traces restore on reload (`chat-transcript-restore.util.ts`); partial in-progress agentic turns persist on disconnect (`server/routes/chat/history.py`, `ws_handler.py`).

**Orchestration composer chip:** one control in the composer shows orchestration **depth** (profile) and live **mode** (planning / delegating / executing). Profile picker reflects **per-agent floored overrides** when an active team plan requires `orchestrated`. Mode label updates only after a successful `switch_mode` (not on failed attempts).

**Thread isolation:** switching agents resets channel thread lists per agent — squad members do not see another agent's Telegram/Discord sessions (`conversation.service.ts`).

---

## UI surface tiers

Floating panels use three CSS contracts. Menus and pickers over chat text use **opaque context-menu** panels; toasts and the run panel stay **glass**.

Full contract, migrated components, and developer rules: **[UI surfaces](../development/ui-surfaces.md)**.

---

## Chat scroll during generation

The message list is the **single scroll surface** for chat (no nested `.chat-messages` scroll). While the agent streams, the view stays pinned to the bottom until you scroll up or use the wheel — then it stops auto-following so you can read earlier messages. Scrolling back to the bottom re-enables follow mode.

Implementation: `message-list.component.ts` (`scrollPinnedToBottom`, `followScrollIfPinned`).

---

## Capability onboarding UI

| Component | Role |
|-----------|------|
| `capability-settings-panel/` | Four workloads × placement cards |
| `day1-coach` | Post-setup guided tour |
| Setup wizard | LAN scan, inference test, Babo Cloud sign-in |

See [Capability profiles](capability-profiles-and-onboarding.md).

---

## Tools page

`features/tools/` — integration cards, MCP list, ClawHub search, schema forms.

---

## Babo Cloud runtime auth

When inference routes through NestJS Babo Cloud (`hosted_babo`, `byok_cloud`, or an `…/api/inference` URL), the Python runtime needs a Bearer on `NLS_INFERENCE_API_KEY`. The Angular UI already sends JWT to NestJS; **`BaboCloudProvisionService`** mirrors that bearer into the local runtime:

| Priority | Bearer source |
|----------|---------------|
| 1 | Stored `nlsk_` key in `nls-config.json` |
| 2 | BYOK provider key (`byok_cloud` profile) |
| 3 | Session JWT from `AuthService` |

Sync runs on app boot, login, token refresh, runtime ready, and after capability profile saves. Hot-reload: `runtime.hotReloadInference` → `POST /admin/hot-reload` updates `vllm_client` without restart.

---

## Build targets

| Command | Output |
|---------|--------|
| `ng build` | Production web |
| `ng build --configuration=electron` | Bundled into desktop |
| `ng serve` | Dev server :4200 |

---

## Related

- [Deployment topologies](deployment-topologies.md)
- [Desktop architecture](desktop.md)
- [UI surfaces](../development/ui-surfaces.md)
- [WebSocket events](../reference/websocket-events.md)
