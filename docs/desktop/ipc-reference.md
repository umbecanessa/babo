# Electron IPC reference

Bridge between Angular renderer and Electron main process.

**Preload:** `desktop/electron/preload.ts` → `window.nls`  
**Handlers:** `desktop/electron/ipc-handlers.ts`

---

## Renderer API (`window.nls`)

| Property / method | IPC channel | Purpose |
|-------------------|-------------|---------|
| `platform`, `isDesktop` | — | OS + desktop shell flag |
| `boot`, `getBoot()` | `config:boot` (sync) | NestJS/runtime URLs from `%APPDATA%/babo-desktop/nls-config.json` |
| `getVersion()` | `app:version` | App version |
| `config.get/set/reset` | `config:*` | Persisted settings |
| `config.testConnection(url)` | `config:test-connection` | Inference ping |
| `setup.check/start/reset` | `setup:*` | Python venv + bundled runtimes wizard |
| `capabilities.scanDevice()` | `capabilities:scan-device` | Local GPU/RAM probe |
| `capabilities.probeLan(host, secret?)` | `capabilities:probe-lan` | GX10 / LAN worker probe |
| `capabilities.recommend(scan, secret?)` | `capabilities:recommend` | Profile recommendation |
| `capabilities.testInference(url, key?)` | `capabilities:test-inference` | Model list + latency |
| `capabilities.prefetchVision()` | `capabilities:prefetch-vision` | Download vision weights |
| `capabilities.applyProfile(profile)` | `capabilities:apply-profile` | Write capability profile |
| `runtime.getStatus/start/stop/restart/getLogs` | `runtime:*` | Uvicorn lifecycle + logs |
| `runtime.hotReloadInference(body)` | `runtime:hot-reload-inference` | Hot-swap inference config |
| `getUrls()` | `urls:get` | `{ runtimeUrl, nestjsUrl, apiUrl, wsUrl }` |
| `backend.ping(nestjsUrl?)` | `backend:ping` | NestJS health |
| `readFile/writeFile/readDir/stat` | `fs:*` | Permission-gated FS |
| `showOpenDialog/showSaveDialog` | `dialog:*` | Native dialogs |
| `execCommand(cmd, cwd?)` | `shell:exec` | Permission-gated shell |
| `readClipboard/writeClipboard` | `clipboard:*` | Clipboard |
| `getSystemInfo()` | `system:info` | Host metadata |
| `debug.getSummary()` | `debug:summary` | Recent errors + exportable artifacts |
| `debug.revealUserData()` | `debug:reveal-user-data` | Open userData in file manager |
| `debug.exportArtifact(kind, agentId?)` | `debug:export-artifact` | Save dialog → single log/state export |
| `debug.exportFullBundle()` | `debug:export-full` | Save dialog → full support `.zip` |
| `showNotification(title, body)` | `notification:show` | OS notification |
| `permissions.getAll/getProfiles/applyProfile/reset/request` | `permissions:*` | Capability prompts |
| `update.check/download/install/snooze/getStatus` | `update:*` | Auto-update |
| `openAuthWindow(url)` | `browser:open-auth-window` | OAuth |
| `openExternal(url)` | `shell:open-external` | Default browser |
| `setBrowserCookies(...)` | `browser:set-cookies` | Webview partition |
| `listMcpServers/addMcpServer/removeMcpServer` | `mcp:*` | MCP stubs |

Deprecated aliases: `getPermissions`, `requestPermission` → use `permissions.*`.

---

## Main → renderer events

Subscribe via `window.nls.on(channel, callback)`:

| Channel | When |
|---------|------|
| `runtime:status-changed` | Python process up/down |
| `runtime:log` | Stdout/stderr line |
| `setup:progress` | Venv setup % |
| `setup:log` | Setup log line |
| `vision:prefetch-progress` | Vision model download |
| `permission:requested` | User approval needed |
| `config:changed` | Settings persisted |
| `update:*` | Update lifecycle |
| `notification:clicked` | OS notification |
| `mcp:tool-discovered` | MCP stub event |

---

## Runtime environment mapping

`ConfigManager.getRuntimeEnv()` spawns Python with:

- Inference vars from wizard / capability profile
- `NESTJS_URL` = backend URL (starts relay)
- `NLS_DATA_DIR` = userData/data when packaged
- `NLS_NODE_BIN`, `NLS_NPM_BIN`, `NLS_PWSH_BIN` when bundled runtimes are installed

See [Desktop configuration](../configuration/desktop.md) and [Platform shell on Windows](../architecture/platform-shell-and-windows.md).

---

## Security model

- Renderer has **no** direct Node access except exposed IPC
- `PermissionManager` gates FS, shell, notifications
- Runtime binds localhost only by default

---

## Debug export kinds (`debug.exportArtifact`)

| `kind` | Export |
|--------|--------|
| `runtime_log` | `runtime.log` (tail if huge) |
| `setup_log` | `setup.log` |
| `electron_log` | Latest desktop main log |
| `desktop_config` | Redacted `nls-config.json` |
| `agent_transcript` | `chat_transcript.jsonl` (requires `agentId`) |
| `agent_sessions` | `sessions/` folder as zip |
| `agent_agentic_logs` | `agentic_logs/` as zip |
| `agent_state` | Agent metadata, config, sessions, plans, memory (zip) |

---

## Related

- [Desktop support & debug export](../guides/desktop-support-debug.md)
- [Desktop configuration](../configuration/desktop.md)
- [Capability profiles](../architecture/capability-profiles-and-onboarding.md)
- [Deployment topologies](../architecture/deployment-topologies.md)
