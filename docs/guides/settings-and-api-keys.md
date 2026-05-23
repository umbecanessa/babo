# Settings & API keys

Configure your account and programmatic access to the **local Python runtime**.

**Routes:** `/settings`, `/settings/api-keys`

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
| JWT (login) | NestJS + Socket.IO + `/api/rt` |
| `nlsk_` API key | Python runtime HTTP/WS directly |

For hosted web UI, use JWT. For local scripts on the same machine as the runtime, use API keys.

---

## Related

- [Auth & access](../architecture/auth-and-access.md)
- [NestJS API](../reference/nestjs-api.md)
- [Environment variables](../configuration/environment-variables.md)
