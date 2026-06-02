# Desktop app configuration

The Babo desktop app is an **Electron** shell around the Angular UI and a **local Python runtime** (`server.main:app` via uvicorn).

---

## First-run wizard (UI)

The onboarding wizard in `desktop/src/app/features/setup/` walks through capability placement and account setup. Typical flow:

| Phase | What happens |
|-------|----------------|
| Welcome & prepare | App checks whether the Python venv exists; may start background setup |
| Device scan | Local GPU/RAM probe and optional LAN inference discovery |
| Capability cards | Choose where **brain** (inference), **features**, and **vision** run |
| Sign-in / billing | Babo Cloud account (optional) |
| Agent naming | First agent display name |
| Launch | Start uvicorn on `127.0.0.1:9222` (configurable) |

Settings → **Capabilities** can change the profile later (`capabilityProfile` in `nls-config.json`).

See [Capability profiles & onboarding](../architecture/capability-profiles-and-onboarding.md).

---

## First-run setup (Python pipeline)

Behind the wizard, `VenvManager.setup()` in `desktop/electron/venv-manager.ts` provisions the runtime:

| Step | Action | Progress (approx.) |
|------|--------|-------------------|
| 1 | Detect or download **standalone Python 3.12.12** | 5–15% |
| 2 | Create venv under `%AppData%/babo-desktop/python-env` | 20–35% |
| 3 | `pip install -r requirements-desktop.txt` | 45–84% |
| 4 | Playwright Chromium (browser tool) | ~82% |
| 5 | Optional Moondream prefetch (if user enables ambient vision) | 85% |
| 6 | CUDA PyTorch upgrade when NVIDIA GPU detected | ~88% |
| 7 | **Standalone Node.js 20.18.3** (skill bridges) | ~92% |
| 8 | **Standalone PowerShell 7.5.7** (Windows only — agent `bash()` shell) | ~95% |
| 9 | Seed data directories + genesis templates | ~98% |

On later launches, `checkDepsSync()` re-runs pip when `requirements-desktop.txt` changes and re-ensures Node/PowerShell if missing.

**Internet required** for first-run downloads (~2 GB+ with optional vision/CUDA).

Manual Python is only needed if standalone download fails — install from [python.org](https://python.org) with “Add to PATH”.

---

## Bundled standalone runtimes

Downloaded to Electron **userData** (not inside the installer):

| Runtime | Path (under userData) | Env var injected at runtime start |
|---------|----------------------|-----------------------------------|
| Python venv | `python-env/` | (venv `python.exe` is the process) |
| Node.js | `node-standalone/node/` | `NLS_NODE_BIN`, `NLS_NPM_BIN` |
| PowerShell 7 (Windows) | `powershell-standalone/pwsh/pwsh.exe` | `NLS_PWSH_BIN` |

On Windows, the agent `bash()` tool runs **PowerShell 7** when `NLS_PWSH_BIN` or system `pwsh` is available — not Linux bash. See [Platform shell on Windows](../architecture/platform-shell-and-windows.md).

---

## Build commands

From the repo root:

```powershell
cd desktop
npm install
npm run build              # Angular (electron) + Electron TS
npm run dist:win           # Windows NSIS installer → desktop/release/
```

**Quick local test (no installer):**

```powershell
cd desktop
.\build-local.ps1          # unpacked app in release-build/win-unpacked/Babo.exe
.\build-local.ps1 -Installer   # full Babo-Setup-x.y.z.exe
```

Before release builds, CI runs `python scripts/regenerate-genesis.py` and `python scripts/ensure-desktop-icons.py`.

**Publish via GitHub Actions (recommended):**

```powershell
.\scripts\tag-desktop-release.ps1
```

That bumps `desktop/package.json`, commits on `main`, pushes tag `vX.Y.Z`, and triggers [`.github/workflows/release-desktop.yml`](https://github.com/umbecanessa/babo/blob/main/.github/workflows/release-desktop.yml) to build **Windows + macOS** and publish the GitHub Release (`latest.yml` / `latest-mac.yml`).

**Linux:** build locally with `npm run dist:linux` (AppImage/deb) — not produced by the release workflow today.

Re-run a failed build from **Actions → Release Desktop → Run workflow** (use the same tag).

Optional repo secrets for code signing: `WINDOWS_CERTIFICATE_*`, `APPLE_CERTIFICATE_*`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`. Unsigned builds still publish and auto-update works.

See also `desktop/BUILD-MAC.md` for macOS-only builds via `build-mac.sh`.

---

## Runtime management

`desktop/electron/` modules:

| Module | Role |
|--------|------|
| `config-manager.ts` | Inference, backend, capability profile → runtime env |
| `venv-manager.ts` | Python venv, pip, bundled Node/PowerShell, browser engine |
| `runtime-manager.ts` | Start/stop uvicorn, device lease heartbeats, log tail |
| `update-manager.ts` | GitHub releases auto-update |
| `capability-scanner.ts` | Device + LAN probe for onboarding |
| `capability-recommender.ts` | Profile recommendations from scan results |
| `permission-manager.ts` | Filesystem/shell permission profiles |
| `ipc-handlers.ts` | IPC wiring for setup, capabilities, config |

IPC channels for setup: `setup:start`, `setup:progress`, `setup:log`, `setup:reset`.

---

## Environment passed to runtime

`ConfigManager.getRuntimeEnv()` merges with the process environment when spawning Python. Key vars:

```text
NLS_PRODUCT_MODE=1
NLS_VLLM_BASE_URL=<inferenceUrl>
NLS_HF_MODEL=<inferenceModel>
NLS_INFERENCE_API_KEY=<optional>
NLS_SLEEP_ENABLED=true
NESTJS_URL=<nestjsUrl>
NLS_DATA_DIR=<userData>/data
NLS_PORT=<runtimePort>
NLS_HOST=127.0.0.1
NLS_SHARED_SECRET=<runtimeSharedSecret>
NLS_NODE_BIN=<bundled node.exe when present>
NLS_NPM_BIN=<bundled npm when present>
NLS_PWSH_BIN=<bundled pwsh.exe on Windows when present>
NLS_BROWSER_CDP_URL=http://127.0.0.1:9245
# Capability profile placements (when configured):
NLS_GPU_WORKER_URL, NLS_GPU_WORKER_SECRET, …
```

Full list: [Environment (complete)](../reference/environment-complete.md).

---

## Desktop userData layout

| Path | Purpose |
|------|---------|
| `nls-config.json` | Inference, backend, capability profile, setup flags |
| `python-env/` | Python venv |
| `node-standalone/` | Bundled Node.js |
| `powershell-standalone/` | Bundled PowerShell 7 (Windows) |
| `data/` | Agent runtime data (`NLS_DATA_DIR`) — see [Data directory](../reference/data-directory.md) |
| `setup.log` | First-run / deps sync log |
| `runtime.log` | uvicorn runtime log |
| `setup-state.json` | Setup progress hash |

---

## Updates

Pre-built installers: **[github.com/umbecanessa/babo/releases](https://github.com/umbecanessa/babo/releases)** (Windows + macOS from CI).

The desktop app checks on launch (30 s delay) and every 4 hours (`update-manager.ts`). End users do not need a GitHub token.

---

## Related

- [Installation](../getting-started/installation.md)
- [Architecture — desktop](../architecture/desktop.md)
- [Electron IPC reference](../desktop/ipc-reference.md)
- [Platform shell on Windows](../architecture/platform-shell-and-windows.md)
- [Device lease](../architecture/device-lease.md)
