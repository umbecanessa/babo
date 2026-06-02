# Sequence: relay registration & web chat join

End-to-end path when a user chats on **hosted web** while the **desktop** runs the Python runtime.

---

## 1. Desktop connects relay

```mermaid
sequenceDiagram
    participant Desktop as Electron / Python
    participant Nest as NestJS
    participant DB as PostgreSQL

    Desktop->>Nest: WS /api/channels/relay/{runtimeAgentId}?secret=
    Nest->>Nest: ChannelsService.registerRelaySocket()
    Desktop->>Nest: {"type":"agent_info","agent_id":"..."}
    Note over Nest: relaySockets map updated
```

---

## 2. User opens chat in browser

```mermaid
sequenceDiagram
    participant UI as Angular
    participant Chat as ChatGateway /chat
    participant Agents as AgentsService
    participant Ch as ChannelsService
    participant Desktop as Desktop Python

    UI->>Chat: Socket.IO connect (JWT in auth)
    Chat->>Chat: verify JWT → userId
    UI->>Chat: emit join { agentId: UUID }
    Chat->>Agents: getRuntimeAgentId(userId, UUID)
    Agents-->>Chat: runtimeAgentId
    Chat->>Ch: hasRelaySocket(runtimeAgentId)?
    Ch-->>Chat: true
    Chat->>Ch: addBroadcastListener()
    Chat-->>UI: joined { agentId, runtimeAgentId }
```

---

## 3. User sends a message (relay mode)

```mermaid
sequenceDiagram
    participant UI as Angular
    participant Chat as ChatGateway
    participant Ch as ChannelsService
    participant Desktop as AgentRuntime
    participant LLM as Inference API

    UI->>Chat: message { content: "..." }
    Chat->>Ch: pushChatToRelay(content, sessionKey, requestId)
    Ch->>Desktop: WS chat_request
    Desktop->>Desktop: process_message_agentic_async()
    Desktop->>LLM: chat completions (BYO)
    LLM-->>Desktop: tokens / tool calls
    Desktop->>Ch: WS chat_response { request_id, content, nls }
    Ch->>Chat: onChatResponse callback
    Chat-->>UI: runtime { type: response_end, ... }
```

---

## 4. Channel webhook while relay online

```mermaid
sequenceDiagram
    participant TG as Telegram
    participant Nest as ChannelsController
    participant Ch as ChannelsService
    participant Desktop as Python skill webhook

    TG->>Nest: POST /api/channels/webhook/telegram/{runtimeAgentId}
    Nest->>Ch: pushToRelayByAgentId()
    Ch->>Desktop: WS channel_message
    Desktop->>Desktop: POST /skills/telegram-channel/webhook/{id}
    Desktop->>Desktop: agentic reply (if policy allows)
    Desktop->>TG: Bot API sendMessage (outbound)
```

---

## 5. Webhook when desktop offline

```mermaid
sequenceDiagram
    participant TG as Telegram
    participant Nest as ChannelsService
    participant DB as PendingChannelMessage

    TG->>Nest: POST webhook
    Nest->>Ch: pushToRelay → false
    Nest->>DB: insert pending row
    Nest-->>TG: 200 { delivered: false }
    Note over DB: Drained on relay connect or GET /pending
```

---

## Related

- [Channels API examples](../examples/nestjs-channels-api.md)
- [Chat module](../nestjs-modules/chat.md)
- [Relay protocol](../../reference/relay-protocol.md)
- [Deployment topologies](../deployment-topologies.md)
