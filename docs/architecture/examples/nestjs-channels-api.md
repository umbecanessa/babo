# NestJS Channels API — request/response examples

Base URL: `https://<api-host>/api`

---

## Activate email channel (user JWT)

**Request**

```http
POST /api/channels/email/activate/a1b2c3d4-e5f6-7890-abcd-ef1234567890
Authorization: Bearer eyJhbG...
```

`agentId` is Postgres UUID; service resolves `runtimeAgentId`.

**Response** `200`

```json
{
  "alias": "aria-a1b2c3d@inbound.yourdomain.com",
  "agentId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "runtimeAgentId": "agent_7f3a9c2e",
  "status": "active"
}
```

**Errors**

| Status | Body hint |
|--------|-----------|
| `503` | Resend not configured (`RESEND_API_KEY`) |
| `403` | Not your agent |

Then Python is called: `POST /skills/email-channel/activate/{runtimeAgentId}`.

---

## Send email (runtime secret)

Called by **Python** when the agent sends mail, not by the browser.

**Request**

```http
POST /api/channels/email/send
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{
  "from": "aria-a1b2c3d@inbound.yourdomain.com",
  "to": "user@example.com",
  "subject": "Re: Project update",
  "html": "<p>Here is the summary...</p>",
  "in_reply_to": "<message-id@mail.example>"
}
```

**Response** `200`

```json
{
  "id": "resend-message-id",
  "status": "sent"
}
```

---

## Channel webhook (Telegram / WhatsApp)

External provider posts to NestJS (public URL). `agentId` in path is **`runtimeAgentId`**.

**Request**

```http
POST /api/channels/webhook/telegram/agent_7f3a9c2e
Content-Type: application/json

{
  "update_id": 123456789,
  "message": {
    "message_id": 1,
    "from": { "id": 999, "first_name": "User" },
    "chat": { "id": 999, "type": "private" },
    "text": "Hello Aria"
  }
}
```

**Response** `200` (immediate ack)

```json
{
  "ok": true,
  "delivered": true
}
```

| `delivered` | Meaning |
|-------------|---------|
| `true` | Relay WS pushed `channel_message` to desktop |
| `false` | Queued in `PendingChannelMessage` until relay connects |

**Relay payload** (desktop receives on WS):

```json
{
  "type": "channel_message",
  "channel": "telegram",
  "payload": { "...": "original webhook body" }
}
```

Desktop forwards to: `POST http://127.0.0.1:9222/skills/telegram-channel/webhook/{runtimeAgentId}`.

---

## Drain pending messages (JWT)

**Request**

```http
GET /api/channels/pending/agent_7f3a9c2e
Authorization: Bearer eyJhbG...
```

**Response** `200`

```json
{
  "drained": 3,
  "messages": [
    {
      "id": "pending-uuid",
      "channel": "telegram",
      "payload": { "...": "..." },
      "createdAt": "2026-05-23T10:05:00.000Z"
    }
  ]
}
```

---

## Internal soul package hook (runtime secret)

**Request**

```http
POST /api/channels/internal/agents/agent_7f3a9c2e/soul-packages
Authorization: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{
  "chainHeight": 42,
  "checksum": "sha256:...",
  "label": "post-sleep"
}
```

**Response** `201`

```json
{
  "id": "soul-package-uuid",
  "agentId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "chainHeight": 42
}
```

---

## Relay WebSocket (desktop registration)

Not REST — documented here for pairing with webhooks.

```http
GET /api/channels/relay/agent_7f3a9c2e?secret=<RUNTIME_SHARED_SECRET>
Upgrade: websocket
```

**Desktop → NestJS** (after connect):

```json
{
  "type": "agent_info",
  "agent_id": "agent_7f3a9c2e",
  "status": "alive"
}
```

**NestJS → desktop** (chat from web):

```json
{
  "type": "chat_request",
  "content": "Summarize my inbox",
  "session_key": "web:remote:socket-id",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "channel_type": "web"
}
```

**Desktop → NestJS** (reply):

```json
{
  "type": "chat_response",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "content": "You have 3 unread threads...",
  "nls": { "signals": [], "hormones": {} }
}
```

---

## Related

- [Channels module](../nestjs-modules/channels.md)
- [Channels & webhooks](../channels-and-webhooks.md)
- [Relay join sequence](../sequences/relay-join.md)
- [Relay protocol](../../reference/relay-protocol.md)
