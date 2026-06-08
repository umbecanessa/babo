# Settings & API keys

Configure programmatic access to the **local Python runtime** and automation keys.

**Routes:** `/settings/api-keys` · **Full Settings guide:** [Settings](settings.md)

**Desktop:** Settings also includes **Support & Debug** (log export, full debug bundle). See [Desktop support & debug export](desktop-support-debug.md).

---

## User settings

`GET/PUT /api/settings` — per-user JSON blob (`UserSettings` in Postgres).

Stores UI preferences and account-level options configured in the Settings page.

---

## API keys

Create keys for automation against the runtime **without** the NestJS chat relay.

### Create a key

1. Open **Settings → API keys**
2. Create key — copy the secret immediately (shown once)
3. Keys are stored **hashed** server-side; prefix `nlsk_`

### Use a key

```bash
curl -H "Authorization: Bearer nlsk_YOUR_KEY" \
  http://127.0.0.1:9222/agents
```

Works when Python binds to localhost and middleware accepts API keys (`server/middleware/auth.py`).

### Rate limits

Optional per-key RPM in `ApiKey` model — enforced by `api-keys` service.

---

## NestJS vs Python keys

| Key type | Works on |
|----------|----------|
| JWT (login) | NestJS + Socket.IO + `/api/rt` + Babo Cloud `/api/inference` and `/api/gpu` |
| `nlsk_` API key | Python runtime HTTP/WS directly **and** Babo Cloud inference/GPU when used as `NLS_INFERENCE_API_KEY` |

For hosted web UI, use JWT. For local scripts on the same machine as the runtime, use API keys.

### Desktop + Babo Cloud

When inference is relayed through NestJS, the desktop **syncs JWT** (or a stored `nlsk_` key) into `NLS_INFERENCE_API_KEY` for the local Python process. Create an `nlsk_` key here if you want a long-lived bearer instead of session JWT — the provision service prefers an explicit `nlsk_` in `nls-config.json` over JWT when present.

---

## Related

- [Auth & access](../architecture/auth-and-access.md)
- [NestJS API](../reference/nestjs-api.md)
- [Environment variables](../configuration/environment-variables.md)
