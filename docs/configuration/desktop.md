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

**Publish via GitHub Actions (recommended):**

One command from the repo root (patch bump by default):

```powershell
.\scripts\tag-desktop-release.ps1
```

```bash
./scripts/tag-desktop-release.sh
```

Examples: `-Bump minor`, `-Version 1.9.7` / `--version 1.9.7`, `-DryRun` / `--dry-run`.

That bumps `desktop/package.json`, commits on `main`, pushes tag `vX.Y.Z`, and triggers [`.github/workflows/release-desktop.yml`](../../.github/workflows/release-desktop.yml) to build Windows + macOS and publish the GitHub Release (`latest.yml` / `latest-mac.yml`).

Re-run a failed build from **Actions → Release Desktop → Run workflow** (use the same tag).

Optional repo secrets for code signing: `WINDOWS_CERTIFICATE_*`, `APPLE_CERTIFICATE_*`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`. Unsigned builds still publish and auto-update works.

**Manual release (local):**

```powershell
cd desktop
.\release.ps1              # patch bump, build, commit, gh release
.\release.ps1 -SkipGit     # build only, no git push
.\release.ps1 -Version 1.9.7
```

**Windows + Mac via Mac Mini SSH:**

```powershell
.\release-all.ps1
```

See also `desktop/BUILD-MAC.md` for macOS-only builds via `build-mac.sh`.

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

Releases publish to **public** GitHub Releases on `umbecanessa/babo`. The desktop app checks on launch and every 4 hours (`desktop/electron/update-manager.ts`). End users do not need a GitHub token.

CI builds must upload `latest.yml` (Windows) and, when macOS is included, `latest-mac.yml` plus installers — the Release Desktop workflow does this automatically.

---

## Related

- [Installation](../getting-started/installation.md)
- [Architecture — desktop](../architecture/desktop.md)
