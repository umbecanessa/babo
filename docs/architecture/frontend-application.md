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
| `/dashboard` | Agent list + relay status |
| `/create` | Genesis wizard |
| `/chat/:agentId` | Main chat |
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
| `WebSocketService` | Chat — Socket.IO or raw WS |
| `AuthService` | JWT storage |
| `TerminalService` | User shell via `/terminal` |
| `FilesystemService` | IDE — IPC in Electron, API in web |
| `ChatWorkbenchService` | Workbench panels |
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

`features/projects/` — board, timeline, files, teams.

Uses `ProjectService` + runtime APIs for plan/team state.

---

## Tools page

`features/tools/` — integration cards, MCP list, ClawHub search, schema forms.

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
- [WebSocket events](../reference/websocket-events.md)
