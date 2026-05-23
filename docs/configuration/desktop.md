# Desktop app configuration

The Babo desktop app is an **Electron** shell around the Angular UI and a **local Python runtime**.

---

## First-run wizard

| Step | Action |
|------|--------|
| Python | Create venv, install `requirements-desktop.txt`, Playwright browsers |
| Inference | URL, model, API key |
| Backend | NestJS URL (auth + relay) |
| Launch | Start uvicorn on port 9222 |

Config persists in Electron user data — editable in Settings.

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
.\build-local.ps1          # unpacked app in release/win-unpacked/Babo.exe
.\build-local.ps1 -Installer   # full Babo-Setup-x.y.z.exe
```

**Publish a GitHub release (Windows):**

```powershell
cd desktop
.\release.ps1              # patch bump, build, commit, gh release
.\release.ps1 -SkipGit     # build only, no git push
.\release.ps1 -Version 1.9.7
```

**Windows + Mac (requires SSH to a Mac build machine):**

```powershell
.\release-all.ps1
```

See also `desktop/BUILD-MAC.md` for macOS-only builds via `build-mac.sh`.

macOS builds must run on a Mac (`npm run dist:mac` or `build-mac.sh`).

---

## Runtime management

`desktop/electron/` modules:

| Module | Role |
|--------|------|
| `config-manager.ts` | Inference + backend settings → env vars |
| `venv-manager.ts` | Python venv create/install |
| `runtime-manager.ts` | Start/stop uvicorn |
| `update-manager.ts` | GitHub releases auto-update |

---

## Environment passed to runtime

When spawning Python, desktop sets:

```text
NLS_PRODUCT_MODE=1
NLS_VLLM_BASE_URL=<inferenceUrl>
NLS_HF_MODEL=<inferenceModel>
NLS_INFERENCE_API_KEY=<optional>
NLS_SLEEP_ENABLED=true
```

---

## Updates

Releases publish to GitHub. Auto-update checks on launch (requires `GH_TOKEN` or `GITHUB_TOKEN` for private rate limits in CI — not needed for end users on public repo).

---

## Related

- [Installation](../getting-started/installation.md)
- [Architecture — desktop](../architecture/desktop.md)
