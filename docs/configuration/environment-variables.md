# Environment variables

## Python runtime (`server/` + `nls/`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NLS_VLLM_BASE_URL` | — | OpenAI-compatible inference base URL |
| `NLS_HF_MODEL` | — | Model id for chat/completions |
| `NLS_INFERENCE_API_KEY` | — | Bearer token for inference provider |
| `NLS_SLEEP_ENABLED` | `true` | Enable consolidation sleep |
| `NLS_DEFAULT_GENESIS` | `standard-v1` | Default agent template |
| `NLS_DATA_DIR` | `data` | Runtime data root |
| `NLS_PRODUCT_MODE` | `1` | Product runtime profile (BYO inference + consolidation sleep) |
| `NLS_SHARED_SECRET` | — | Shared secret for NestJS → runtime auth (must match backend `RUNTIME_SHARED_SECRET`) |
| `NLS_SERVE_HOST` | `0.0.0.0` | Listen host |
| `NLS_SERVE_PORT` | `8443` | Listen port |

---

## NestJS backend (`backend/`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `JWT_SECRET` | — | Access token signing secret (64+ chars in production) |
| `JWT_REFRESH_SECRET` | — | Refresh token signing secret |
| `JWT_EXPIRATION` | `15m` | Access token TTL |
| `JWT_REFRESH_EXPIRATION` | `7d` | Refresh token TTL |
| `RUNTIME_URL` | `http://127.0.0.1:9222` | Python Babo runtime HTTP base (no trailing slash) |
| `RUNTIME_SHARED_SECRET` | — | Shared secret sent as `X-Runtime-Secret` to the Python runtime |
| `PORT` | `3000` | NestJS listen port |
| `RESEND_API_KEY` | — | Resend API key (email channel) |
| `RESEND_INBOUND_DOMAIN` | — | Inbound email domain (e.g. `inbox.example.com`) |

Aliases `BABO_RUNTIME_URL` and `BABO_SHARED_SECRET` are accepted as fallbacks for `RUNTIME_*`.

See `backend/.env.example` for a starter template.

### Railway (copy-paste)

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
RUNTIME_SHARED_SECRET=nls-dev-secret
RUNTIME_URL=http://stadionweg.mercusysddns.com:4443
JWT_EXPIRATION=4h
JWT_REFRESH_EXPIRATION=7d
JWT_REFRESH_SECRET=nls-dev-refresh-secret-change-in-production-64chars-min-req
JWT_SECRET=nls-dev-jwt-secret-change-in-production-64chars-minimum-required
PORT=3000
RESEND_API_KEY=re_...
RESEND_INBOUND_DOMAIN=inbox.babo.agency
```

On the **Python runtime** host (port `4443` in the example above), set:

```env
NLS_SHARED_SECRET=nls-dev-secret
NLS_PRODUCT_MODE=1
NLS_SERVE_PORT=4443
```

`NLS_SHARED_SECRET` must equal `RUNTIME_SHARED_SECRET`.

---

## Desktop (Electron)

Set via first-run wizard or persisted config:

| Setting | Maps to |
|---------|---------|
| `inferenceUrl` | `NLS_VLLM_BASE_URL` |
| `inferenceModel` | `NLS_HF_MODEL` |
| `inferenceApiKey` | `NLS_INFERENCE_API_KEY` |
| `nestjsUrl` | Backend API base |

---

## Docker Compose (Postgres only)

Root `docker-compose.yml`:

```yaml
POSTGRES_USER=nls
POSTGRES_PASSWORD=nls
POSTGRES_DB=nls
# port 5432
```

---

## Related

- [Inference providers](inference-providers.md)
- [Self-hosting](self-hosting.md)
