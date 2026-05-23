# Chat module

**Path:** `backend/src/chat/`

Socket.IO gateway for web chat: JWT on connect, join agent room, forward messages to Python or **desktop relay**.

---

## Files

| File | Role |
|------|------|
| `chat.module.ts` | Imports `AgentsModule`, `RuntimeModule`, `ChannelsModule`, `JwtModule` |
| `chat.gateway.ts` | `@WebSocketGateway({ namespace: '/chat' })` |

---

## Socket.IO namespace `/chat`

| Client event | Purpose |
|--------------|---------|
| (connect) | Validate JWT from `handshake.auth.token` or `Authorization` |
| `join` | Bind client to `agentId`; choose relay vs direct Python WS |
| `message` | User chat → runtime |
| `remote_chat` | Cross-session / remote delivery |
| `subscribe_broadcasts` | UI events from runtime (`broadcast` relay messages) |
| (disconnect) | Tear down Python WS if any |

**Server emits:** `joined`, `runtime`, `runtime_disconnected`, `error`.

---

## Join logic

```text
if ChannelsService.hasRelaySocket(runtimeAgentId):
    relayMode = true   → pushChatToRelay
else:
    RuntimeService.connectChat()   → WS /ws/chat/{runtimeAgentId}
```

See [Relay protocol](../../reference/relay-protocol.md).

---

## Python integration

**Direct:** outbound `{ type: 'message' | 'command', ... }` on Python chat WS.

**Relay:** `chat_request` / `chat_response` over `WS /api/channels/relay/{id}`.

Slash commands on Python side: [Chat slash commands](../../reference/chat-commands.md).

---

## Environment

- `JWT_SECRET` — connection auth only

---

## Prisma

None direct; may call `AgentsService.updateName` on runtime `name_update` events.

---

## Related

- [Connection & broadcasts](../connection-and-broadcasts.md)
- [WebSocket events](../../reference/websocket-events.md)
