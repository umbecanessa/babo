# Environment variables (complete)

Consolidated reference for Python runtime, NestJS, desktop, and relay.

User-facing subset: [Environment variables](../configuration/environment-variables.md).

---

## Python runtime (`NLS_*`)

From `server/config.py` (`env_prefix = NLS_`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `NLS_PRODUCT_MODE` | `1` | Product profile (BYO inference, consolidation sleep) |
| `NLS_SERVE_HOST` | `0.0.0.0` | HTTP bind host |
| `NLS_SERVE_PORT` | `8443` | HTTP port (desktop often 9222) |
| `NLS_VLLM_BASE_URL` | `http://localhost:8000` | Inference API base |
| `NLS_HF_MODEL` | `gpt-4o-mini` | Model id |
| `NLS_INFERENCE_API_KEY` | — | Bearer token (provider key, `nlsk_`, or user JWT for Babo Cloud relay) |
| `NLS_DEFAULT_GENESIS` | `standard-v1` | Default template |
| `NLS_DATA_DIR` | `./data` | Runtime data root |
| `NLS_SLEEP_ENABLED` | `true` | Sleep scheduler |
| `NLS_AGENT_WHITELIST` | — | Comma-separated auto-load ids |
| `NLS_SHARED_SECRET` | — | Auth for NestJS → runtime |
| `NLS_API_KEY_PREFIX` | `nlsk_` | User API key prefix |
| `NLS_DEFAULT_MAX_TOKENS` | `512` | Completion default |
| `NLS_DEFAULT_TEMPERATURE` | `0.7` | Sampling |
| `NLS_DEFAULT_TOP_P` | `0.9` | Top-p |
| `NLS_MAX_AGENTS_VRAM` | `50` | Multi-agent budget hint |
| `NLS_EVICTION_TIMEOUT_MINUTES` | `30` | Soft unload |
| `NLS_EVICTION_HARD_TIMEOUT_HOURS` | `24` | Hard unload |
| `NLS_DREAM_TICK_INTERVAL` | `30` | DMN tick seconds |

### Additional Python (not in ServerSettings)

| Variable | Purpose |
|----------|---------|
| `NLS_CONSCIOUSNESS_ENABLED` | Inner loop scheduler (`true`/`false`) |
| `NESTJS_URL` | Start ChannelRelayClient to cloud |
| `RUNTIME_SHARED_SECRET` | Alias for relay secret |
| `PORT` | Webhook URL construction for skills |
| `NLS_NODE_BIN`, `NLS_NPM_BIN` | Node paths for skill bridges (desktop bundles Node) |
| `NLS_PWSH_BIN` | Bundled PowerShell 7 on Windows (`bash()` shell) |
| `NLS_BROWSER_CDP_URL` | Electron embedded browser CDP |
| `NLS_WEBHOOK_SECRET` | Webhook HMAC validation |
| `NLS_GPU_WORKER_URL` | Remote Whisper/vision worker |
| `NLS_GPU_WORKER_SECRET` | GPU worker auth |
| `NLS_IS_GPU_WORKER` | This process is GPU worker |
| `PYTHONUNBUFFERED` | Desktop sets `1` for logs |

---

## NestJS (`backend/.env`)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL |
| `JWT_SECRET` | Access token |
| `JWT_REFRESH_SECRET` | Refresh token |
| `JWT_EXPIRATION` | Access TTL |
| `JWT_REFRESH_EXPIRATION` | Refresh TTL |
| `PORT` | Listen port (3000) |
| `RUNTIME_URL` | Direct Python HTTP (optional) |
| `RUNTIME_SHARED_SECRET` | Relay + runtime hooks |
| `BABO_RUNTIME_URL` | Alias for `RUNTIME_URL` |
| `BABO_SHARED_SECRET` | Alias for `RUNTIME_SHARED_SECRET` |
| `RESEND_API_KEY` | Email channel |
| `RESEND_INBOUND_DOMAIN` | Inbound email domain |

---

## Desktop (`config-manager` → runtime env)

| UI field | Env var |
|----------|---------|
| Inference URL | `NLS_VLLM_BASE_URL` |
| Model | `NLS_HF_MODEL` |
| API key | `NLS_INFERENCE_API_KEY` (desktop may hot-sync JWT for Babo Cloud) |
| Backend URL | `NESTJS_URL` |
| Runtime port | `NLS_PORT` |
| Capability profile | `NLS_GPU_WORKER_*`, bridge URLs (from `capabilityProfile`) |
| Bundled runtimes | `NLS_NODE_BIN`, `NLS_NPM_BIN`, `NLS_PWSH_BIN` (Windows) |
| (fixed) | `NLS_PRODUCT_MODE=1`, `NLS_HOST=127.0.0.1`, `NLS_DATA_DIR`, `NLS_BROWSER_CDP_URL` |

See [Desktop configuration](../configuration/desktop.md).

---

## Docker Compose (root)

| Variable | Default |
|----------|---------|
| `POSTGRES_USER` | nls |
| `POSTGRES_PASSWORD` | nls |
| `POSTGRES_DB` | nls |

---

## Alignment checklist (cloud + desktop)

```env
# NestJS
RUNTIME_SHARED_SECRET=your-secret

# Desktop Python
NLS_SHARED_SECRET=your-secret
NESTJS_URL=https://your-api.example.com
```

---

## Related

- [Inference providers](../configuration/inference-providers.md)
- [Cloud deployment](../configuration/cloud-deployment.md)
