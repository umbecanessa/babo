# Electron IPC reference

Bridge between Angular renderer and Electron main process.

**Preload:** `desktop/electron/preload.ts` → `window.nls`  
**Handlers:** `desktop/electron/ipc-handlers.ts`

---

## Renderer API (`window.nls`)

| Property / method | IPC channel | Purpose |
|-------------------|-------------|---------|
| `isDesktop` | — | Always `true` in shell |
| `getVersion()` | `app:version` | App version |
| `config.get/set/reset` | `config:*` | Persisted settings |
| `config.testConnection(url)` | `config:test-connection` | Inference ping |
| `setup.check/start/reset` | `setup:*` | Python venv wizard |
| `runtime.getStatus/start/stop/restart/logs` | `runtime:*` | Uvicorn lifecycle |
| `getUrls()` | `urls:get` | `{ runtimeUrl, nestjsUrl, wsUrl }` |
| `readFile/writeFile/readDir` | `fs:*` | Permission-gated FS |
| `dialog.open/save` | `dialog:*` | Native dialogs |
| `shell.exec` | `shell:exec` | Permission-gated commands |
| `clipboard.read/write` | `clipboard:*` | Clipboard |
| `permissions.get/request` | `permissions:*` | Capability prompts |
| `update.*` | `update:*` | Auto-update |
| `openAuthWindow(url)` | `browser:open-auth-window` | OAuth |
| `openExternal(url)` | `shell:open-external` | Browser |
| `setBrowserCookies(...)` | `browser:set-cookies` | Webview partition |

---

## Main → renderer events

Subscribe via `window.nls.on(channel, callback)`:

| Channel | When |
|---------|------|
| `runtime:status-changed` | Python process up/down |
| `runtime:log` | Stdout/stderr line |
| `setup:progress` | Venv setup % |
| `setup:log` | Setup log line |
| `permission:requested` | User approval needed |
| `update:*` | Update lifecycle |
| `notification:clicked` | OS notification |
| `mcp:tool-discovered` | MCP stub event |

---

## Runtime environment mapping

`ConfigManager.getRuntimeEnv()` spawns Python with:

- Inference vars from wizard
- `NESTJS_URL` = backend URL (starts relay)
- `NLS_DATA_DIR` = userData/data when packaged

See [Desktop architecture](../architecture/desktop.md).

---

## Security model

- Renderer has **no** direct Node access except exposed IPC
- `PermissionManager` gates FS, shell, notifications
- Runtime binds localhost only by default

---

## Related

- [Desktop configuration](../configuration/desktop.md)
- [Deployment topologies](../architecture/deployment-topologies.md)
