# Channels module

**Path:** `backend/src/channels/`

**`@Global()`** — email (Resend), inbound webhooks, pending message queue, and the **desktop relay WebSocket registry**.

**Request/response examples:** [NestJS Channels API](../examples/nestjs-channels-api.md) · **Sequence:** [Relay join](../sequences/relay-join.md)

---

## Files

| File | Role |
|------|------|
| `channels.module.ts` | Global export |
| `channels.controller.ts` | HTTP routes |
| `channels.service.ts` | Resend, relay map, `proxyHttpViaRelay` |

---

## HTTP routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/channels/email/activate/:agentId` | JWT | Create Resend alias |
| POST | `/api/channels/email/send` | `X-Runtime-Secret` | Outbound email from Python |
| POST | `/api/channels/webhook/:channel/:agentId` | public | Provider → relay |
| POST | `/api/channels/email/webhook` | Resend | Inbound email |
| GET | `/api/channels/email/status/:agentId` | JWT | Alias status |
| GET | `/api/channels/pending/:agentId` | JWT | Drain UI queue |
| POST | `/api/channels/pending/drain` | runtime secret | Python drain hook |
| POST | `/api/channels/internal/agents/:agentId/soul-packages` | runtime secret | Soul metadata sync |

---

## Relay WebSocket

Registered in `backend/src/main.ts` (not the controller):

```http
WS /api/channels/relay/{runtimeAgentId}?secret=
```

**Inbound from desktop:** `agent_info`, `chat_response`, `http_proxy_response`, `broadcast`

**Outbound to desktop:** `channel_message`, `chat_request`, `http_proxy`, `skill_install`, `connected`

---

## Pending messages

When desktop offline, webhooks store rows in `PendingChannelMessage`. On relay connect, queue drains to Python skill webhooks.

---

## Environment

| Variable | Purpose |
|----------|---------|
| `RESEND_API_KEY` | Send + fetch inbound |
| `RESEND_INBOUND_DOMAIN` | Alias domain |
| `RUNTIME_SHARED_SECRET` | Relay `secret` query param |

---

## Prisma

`Agent`, `ChannelAlias`, `PendingChannelMessage`, `SoulPackage` (internal hook)

---

## Python paths

| Operation | Path |
|-----------|------|
| Email activate | `POST /skills/email-channel/activate/{runtimeAgentId}` |
| Email status | `GET /skills/email-channel/status/{runtimeAgentId}` |
| Channel ingress | `POST /skills/{channel}-channel/webhook/{id}` |

---

## Related

- [Channels & webhooks](../channels-and-webhooks.md)
- [Runtime proxy](runtime-proxy.md)
