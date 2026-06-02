# Platform shell on Windows

On macOS and Linux, the agent `bash()` tool runs real `/bin/bash`. On **Windows**, `bash()` runs **PowerShell** (prefer **PowerShell 7**).

Implementation: `nls/platform_shell.py` and `nls/tools/agent_tools/bash.py`.

---

## Shell resolution order

1. **`NLS_PWSH_BIN`** — bundled PowerShell from desktop setup (`powershell-standalone/pwsh/pwsh.exe`)
2. **`pwsh` on PATH** — user-installed PowerShell 7
3. **`powershell.exe`** — Windows PowerShell 5.1 (legacy fallback)

The desktop app sets `NLS_PWSH_BIN` when the portable PS7 bundle is installed ([Desktop configuration](../configuration/desktop.md#bundled-standalone-runtimes)).

UTF-8 console encoding is applied via a preamble when invoking PS7.

---

## Instruction skills on Windows

ClawHub / AgentSkill packages often ship `.sh` scripts for Mac/Linux. On Windows:

| Task | Preferred approach |
|------|-------------------|
| REST/API setup (Discord, GitHub, …) | **Python script** (`deploy-*.py`) + JSON files on disk + `httpx` |
| Quick env / file checks | PowerShell via `bash()` |
| Upstream `run.ps1` | Run explicitly when the skill ships it |
| Upstream `.sh` only | Treat as reference; do not fight jq/WSL loops — wrap in Python or `.ps1` |

Policy and post-read nudges: `nls/skills_setup_policy.py`.

**Do not** use `` `u{1F4E2} ``-style escapes in PowerShell (invalid — prints literally). Post JSON from UTF-8 files; use `\u…` in JSON source, not inline PS string rebuilds.

---

## Common fixes

| Problem | Guidance |
|---------|----------|
| `curl` → `Invoke-WebRequest` | Babo rewrites bare `curl` to `curl.exe` in PowerShell commands |
| `curl.exe.exe` | Do not double-append `.exe` |
| Discord `50006` empty message | Use `embeds` (array), not singular `embed` |
| Missing `jq` on Windows | Use Python `json` module or read JSON files directly |

---

## Related

- [Skills system](skills-system.md) — instruction-only vs bundled skills
- [Tools system](tools-system.md) — `bash`, `project_install`, `server_install`
- [Desktop configuration](../configuration/desktop.md)
