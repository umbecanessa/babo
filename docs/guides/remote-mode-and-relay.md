# Remote mode & relay

Use Babo’s **web UI** as a remote control while the **agent brain runs on your desktop**.

**Typical setup:** Babo Cloud (or self-hosted NestJS) for auth + Postgres + WebSocket relay; Electron desktop on your machine runs Python on `127.0.0.1:9222`.

---

## How relay works

```text
Browser (Angular)  ←Socket.IO→  NestJS relay  ←WebSocket→  Desktop Python runtime
```

1. You sign in on the web — JWT stored in browser
2. Desktop app signs in with the same account and maintains relay connection
3. Web **join** event routes chat to your desktop runtime via `runtimeAgentId`
4. Streaming events mirror the local Electron path inside `runtime` envelopes

Details: [Relay protocol](../reference/relay-protocol.md) · [Deployment topologies](../architecture/deployment-topologies.md)

---

## What works in remote mode

| Feature | Web (relay online) | Web (desktop offline) |
|---------|-------------------|----------------------|
| View chat history | Yes (Postgres) | Yes |
| Send messages / tools | Yes | No — queued or error |
| Model picker (LAN/cloud routes) | Yes — routes through desktop runtime | No |
| Projects board sync | Yes | Read-only / stale |
| Memory / Brain views | Partial — needs runtime APIs | Limited |
| Integrations (channels) | Yes — channels hit NestJS/webhooks | Yes for inbound; replies need runtime |
| Settings → API keys | Yes | Yes |

---

## Desktop offline UX

When relay drops:

- Chat shows **runtime disconnected** banner
- Agent cards on dashboard show offline state
- Inbound channel messages may queue until desktop reconnects
- Transcript reload from DB still works; live agentic loop does not

**Fix:** Open the desktop app on the machine that owns the runtime; confirm same account; check backend `RUNTIME_URL` points at reachable sidecar (usually localhost on desktop host only).

---

## Babo Cloud JWT sync

Desktop syncs session JWT (or `nlsk_` key) into `NLS_INFERENCE_API_KEY` so hybrid LAN + cloud inference works after sign-in. The **model picker** on web sends `model` + `model_route` over relay the same as desktop — hybrid catalog requires the desktop sidecar with both LAN and cloud env vars configured. If cloud models fail with 401 after relay reconnect, sign out and in on desktop — see [Desktop configuration](../configuration/desktop.md#babo-cloud-inference-auth) and [Chat → Model picker](chat.md#model-picker).

---

## Self-hosted relay

Same pattern with your NestJS URL:

```env
# backend
RUNTIME_URL=http://127.0.0.1:9222   # on desktop machine
RUNTIME_SHARED_SECRET=...
```

Web UI at `http://localhost:4200` (or deployed frontend) uses `wsUrl` from environment. Desktop must run with matching backend URL in Settings → System.

---

## Security notes

- JWT is short-lived; refresh token in httpOnly cookie (web) or secure store (desktop)
- Relay never executes tools in the cloud — only forwards to your runtime
- API keys (`nlsk_`) work for direct localhost automation bypassing relay

---

## Related

- [Settings](settings.md)
- [Installation](../getting-started/installation.md)
- [Cloud deployment](../configuration/cloud-deployment.md)
- [Troubleshooting](../development/troubleshooting.md)
