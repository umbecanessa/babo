# Settings

Configure Babo at the account and install level.

**Route:** `/settings` · **API keys:** `/settings/api-keys`

Per-agent integrations (WhatsApp, MCP, etc.) live under **Tools**, not Settings.

---

## Desktop sections

| Section | Purpose |
|---------|---------|
| **Models & AI** | Capability profile — inference, vision, transcribe, embeddings tiers |
| **Account** | Email, password, profile |
| **Billing** | Babo Cloud subscription (when enabled) |
| **Integrations** | Shortcut to **Tools → Integrations** — per-agent channel setup lives on the Tools page, not here |
| **System** | Runtime paths, venv, backend URL, logs |
| **Support & Debug** | Log export, debug bundle — see [Desktop support & debug](desktop-support-debug.md) |
| **Permissions** | OS permission profiles (filesystem, shell, browser) |
| **Appearance** | Theme, density |
| **General** | Locale and misc preferences |

---

## Web sections

Hosted web UI users see a subset:

| Section | Purpose |
|---------|---------|
| **Appearance** | Theme |
| **Billing** | Subscription (when enabled) |
| **Integrations** | Account connections; channel skills are configured per agent under **Tools** |
| **API keys** | `nlsk_` automation keys |
| **General** | Preferences |

Web chat requires a **desktop runtime online** for full agent execution — see [Remote mode & relay](remote-mode-and-relay.md).

---

## Models & AI

The capability profile controls **four workloads**:

| Workload | Options (typical) |
|----------|-------------------|
| **Thinking (chat)** | Babo Cloud, local Ollama, LAN server, BYOK |
| **Vision** | Local Moondream, LAN GPU worker, Babo Cloud GPU |
| **Transcribe** | Local Whisper, remote worker |
| **Embeddings** | Local, LAN, or cloud |

Each tier writes runtime env vars (`NLS_VLLM_BASE_URL`, `NLS_LAN_INFERENCE_URL`, `NLS_BABO_CLOUD_INFERENCE_URL`, vision/transcribe URLs). After changes, the desktop may restart the Python sidecar.

**Test Connection** validates inference before save.

Hybrid routing (local + cloud models in one install): [Inference providers](../configuration/inference-providers.md#hybrid-lan-cloud-desktop-v12).

---

## System (desktop)

| Control | Effect |
|---------|---------|
| **Backend URL** | NestJS API for auth, relay, Babo Cloud |
| **Runtime port** | Local Python FastAPI (default 9222) |
| **Python venv** | Path to bundled or custom environment |
| **Restart runtime** | Reload sidecar after env changes |

---

## Permissions (desktop)

Maps Babo agent tools to OS permission tiers:

- Filesystem read/write scope
- Shell execution
- Browser automation
- Network

Profiles align with [Job & Trust](../guides/job-trust-and-squads.md) rails but apply at the install level.

---

## API keys

Create **`nlsk_`** keys for scripts and long agentic runs without session JWT expiry.

Full details: [Settings & API keys](settings-and-api-keys.md).

---

## Related

- [First run & setup](first-run-and-setup.md)
- [Inference providers](../configuration/inference-providers.md)
- [Desktop configuration](../configuration/desktop.md)
- [Remote mode & relay](remote-mode-and-relay.md)
