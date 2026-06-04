# Desktop support & debug export

When something goes wrong in **Babo Desktop**, you can gather logs and agent state from the app instead of hunting through `%AppData%` manually.

**UI:** Settings → **Support & Debug**

---

## What you can do

| Action | Purpose |
|--------|---------|
| **Recent errors** | Highlights ERROR/exception lines from runtime, setup, and desktop logs |
| **Export** (per item) | Save one log, config snapshot, or agent artifact via a save dialog |
| **Export full debug bundle** | One `.zip` for support — recommended when asked to send diagnostics |
| **Open data folder** | Opens the Electron userData directory in your file manager |

---

## Full debug bundle contents

The zip includes (secrets **redacted** in config):

- `manifest.json` — app version, OS, runtime status, parsed errors
- `nls-config.redacted.json` — desktop settings without API keys
- `logs/` — `runtime.log`, `setup.log`, desktop main log (large files are tailed)
- `agents/{id}/` — per agent: metadata, chat transcript, sessions, recent agentic loop logs, brain config, plans, memory indexes (not full workspace)
- `squads/` — squad registry when present

**Excluded:** agent `workspace/` files (can be large or sensitive user projects).

---

## For support requests

1. Reproduce the issue if you can (note what you were doing).
2. Settings → Support & Debug → **Export full debug bundle**.
3. Attach the `.zip` to your message (Discord, email, GitHub issue).

Agents running in Babo are instructed (Cryptex behavioral ring) to suggest this flow when you report desktop problems.

---

## Data locations (reference)

| Path (Windows) | Purpose |
|----------------|---------|
| `%APPDATA%/babo-desktop/runtime.log` | Python runtime |
| `%APPDATA%/babo-desktop/setup.log` | First-run / pip sync |
| `%APPDATA%/babo-desktop/data/agents/{id}/` | Per-agent state |

See [Data directory](../reference/data-directory.md) and [Desktop configuration](../configuration/desktop.md).

---

## Related

- [Settings & API keys](settings-and-api-keys.md)
- [Electron IPC reference](../desktop/ipc-reference.md) — `debug.*` channels
- [Installation](../getting-started/installation.md)
