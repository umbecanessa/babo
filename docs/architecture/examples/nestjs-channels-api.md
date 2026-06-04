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

## Slack Events API webhook

**Request URL** (configured in Slack app):

```text
https://<api-host>/api/channels/webhook/slack/agent_7f3a9c2e
```

**URL verification** (Slack setup):

```http
POST /api/channels/webhook/slack/agent_7f3a9c2e
Content-Type: application/json

{
  "type": "url_verification",
  "challenge": "3cb39193a1ab...",
  "token": "..."
}
```

**Response** `200`

```json
{
  "challenge": "3cb39193a1ab..."
}
```

**Signed event** (after sidecar registered signing secret):

```http
POST /api/channels/webhook/slack/agent_7f3a9c2e
X-Slack-Signature: v0=...
X-Slack-Request-Timestamp: 1710000000
Content-Type: application/json

{
  "type": "event_callback",
  "event": {
    "type": "app_mention",
    "user": "U123",
    "text": "<@UBOT> hello",
    "channel": "C456",
    "ts": "1710000000.000100"
  }
}
```

**Response** `200`

```json
{
  "ok": true,
  "delivered": true,
  "queued": false
}
```

**Errors:** `401 Invalid Slack signature` if secret missing, wrong, or body tampered.

**Register signing secret** (Python sidecar, not browser):

```http
POST /api/channels/slack/register/agent_7f3a9c2e
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{ "signing_secret": "abc123..." }
```

---

## Discord Gateway registration

Called by Python when `discord-channel` starts with relay configured.

**Request**

```http
POST /api/channels/discord/register/agent_7f3a9c2e
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{ "bot_token": "..." }
```

**Response** `200` (after Gateway `READY`)

```json
{
  "ok": true,
  "ready": true,
  "agentId": "agent_7f3a9c2e"
}
```

**Errors:** `400` if token invalid, Gateway timeout, or invalid session.

**Unregister** (sidecar shutdown):

```http
POST /api/channels/discord/unregister/agent_7f3a9c2e
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
```

**Relay payload** for inbound Discord messages:

```json
{
  "type": "channel_message",
  "channel": "discord",
  "payload": {
    "t": "MESSAGE_CREATE",
    "d": {
      "id": "...",
      "channel_id": "...",
      "guild_id": "...",
      "content": "hello",
      "author": { "id": "...", "username": "user" },
      "mentions": []
    }
  }
}
```

Desktop forwards to: `POST http://127.0.0.1:9222/skills/discord-channel/webhook/{runtimeAgentId}`.

---

## Channel scope API (Python runtime)

Mounted by bundled skills on the desktop sidecar (`9222`). `{agentId}` = **runtimeAgentId**.

**List + sync (Discord)**

```http
GET /skills/discord-channel/channels/agent_7f3a9c2e
POST /skills/discord-channel/channels/agent_7f3a9c2e/sync
```

**Response** (excerpt)

```json
{
  "channel": "discord",
  "connected": true,
  "scoped_channel_count": 12,
  "active_channel_count": 2,
  "channels": [
    {
      "id": "1234567890",
      "name": "general",
      "guild_name": "My Server",
      "enabled_desired": true,
      "platform_access": true,
      "effective_enabled": true,
      "require_mention": true
    }
  ]
}
```

**Update desired scope**

```http
PATCH /skills/discord-channel/channels/agent_7f3a9c2e/1234567890
Content-Type: application/json

{
  "enabled": true,
  "require_mention": false
}
```

**Response**

```json
{
  "ok": true,
  "scoped_channels": { "channels": { "...": "..." } },
  "permission_warning": ""
}
```

`permission_warning` may contain text if Discord permission overwrite failed (403).

Slack uses the same paths under `/skills/slack-channel/channels/...`.

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
