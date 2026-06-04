# Channels module

**Path:** `backend/src/channels/`

**`@Global()`** — email (Resend), inbound webhooks, pending message queue, **Discord Gateway**, **Slack signing registry**, and the **desktop relay WebSocket registry**.

**Request/response examples:** [NestJS Channels API](../examples/nestjs-channels-api.md) · **Sequence:** [Relay join](../sequences/relay-join.md)

---

## Files

| File | Role |
|------|------|
| `channels.module.ts` | Global export |
| `channels.controller.ts` | HTTP routes |
| `channels.service.ts` | Resend, relay map, Slack signature verify, `proxyHttpViaRelay` |
| `discord-gateway.service.ts` | Discord Gateway WS per agent; forwards events to relay |

---

## HTTP routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/channels/email/activate/:agentId` | JWT | Create Resend alias |
| POST | `/api/channels/email/send` | `X-Runtime-Secret` | Outbound email from Python |
| POST | `/api/channels/webhook/:channel/:agentId` | public | Provider → relay (Slack sig verified when registered) |
| POST | `/api/channels/discord/register/:agentId` | `X-Runtime-Secret` | Start Discord Gateway for bot token |
| POST | `/api/channels/discord/unregister/:agentId` | `X-Runtime-Secret` | Stop Discord Gateway |
| POST | `/api/channels/slack/register/:agentId` | `X-Runtime-Secret` | Store Slack signing secret for webhook verify |
| POST | `/api/channels/slack/unregister/:agentId` | `X-Runtime-Secret` | Remove Slack signing secret |
| POST | `/api/channels/email/webhook` | Resend | Inbound email |
| GET | `/api/channels/email/status/:agentId` | JWT | Alias status |
| GET | `/api/channels/pending/:agentId` | JWT | Drain UI queue |
| POST | `/api/channels/pending/drain` | runtime secret | Python drain hook |
| POST | `/api/channels/internal/agents/:agentId/soul-packages` | runtime secret | Soul metadata sync |

---

## Discord Gateway service

- Connects to `wss://gateway.discord.gg/?v=10`
- Intents: `GUILDS`, `GUILD_MESSAGES`, `MESSAGE_CONTENT`
- **`register()`** resolves only after Discord `READY` (or error / 20s timeout)
- Forwards dispatch events to `pushToRelayByAgentId(agentId, 'discord', envelope)`
- Reconnect on opcode 7; invalid session on opcode 9

---

## Slack signature verification

- Sidecar posts signing secret to `slack/register`
- `main.ts` preserves **raw body** for `/api/channels/webhook/slack/*` paths
- Controller verifies `X-Slack-Signature` + `X-Slack-Request-Timestamp` before relay
- `url_verification` challenges bypass verification (Slack setup handshake)

---

## Relay WebSocket

Registered in `backend/src/main.ts` (not the controller):

```http
WS /api/channels/relay/{runtimeAgentId}?secret=
```

**Inbound from desktop:** `agent_info`, `chat_response`, `http_proxy_response`, `broadcast`

**Outbound to desktop:** `channel_message`, `chat_request`, `http_proxy`, `skill_install`, `connected`

`channel_message` payload shape:

```json
{
  "type": "channel_message",
  "channel": "slack",
  "payload": { "...": "provider body" }
}
```

Channel values include `telegram`, `slack`, `discord`, `email`, etc.

---

## Pending messages

When desktop offline, webhooks store rows in `PendingChannelMessage`. On relay connect, queue drains to Python skill webhooks.

---

## Environment

| Variable | Purpose |
|----------|---------|
| `RESEND_API_KEY` | Send + fetch inbound |
| `RESEND_INBOUND_DOMAIN` | Alias domain |
| `RUNTIME_SHARED_SECRET` | Relay `secret` query param + runtime hooks |

---

## Prisma

`Agent`, `ChannelAlias`, `PendingChannelMessage`, `SoulPackage` (internal hook)

---

## Python paths

| Operation | Path |
|-----------|------|
| Email activate | `POST /skills/email-channel/activate/{runtimeAgentId}` |
| Email status | `GET /skills/email-channel/status/{runtimeAgentId}` |
| Channel ingress | `POST /skills/{channel}-channel/webhook/{runtimeAgentId}` |
| Discord scope | `GET/POST/PATCH /skills/discord-channel/channels/...` |
| Slack scope | `GET/POST/PATCH /skills/slack-channel/channels/...` |

---

## Related

- [Channels & webhooks](../channels-and-webhooks.md)
- [Discord integration](../../guides/integrations/discord.md)
- [Slack integration](../../guides/integrations/slack.md)
- [Runtime proxy](runtime-proxy.md)
