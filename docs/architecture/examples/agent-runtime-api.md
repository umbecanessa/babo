# Agent runtime — request/response examples

Python FastAPI on desktop (`http://127.0.0.1:9222` by default).  
Auth: `X-Runtime-Secret` or `Authorization: Bearer nlsk_...`

These are the paths the **brain** exposes; NestJS proxies many of them when using hosted web + relay.

---

## Create agent (local)

**Request**

```http
POST /agents
Content-Type: application/json
X-Runtime-Secret: ...

{
  "genesisVersion": "standard-v1",
  "name": "Aria",
  "sovereignty": "local",
  "ownerEmail": "you@example.com"
}
```

**Response** `200`

```json
{
  "agent_id": "agent_7f3a9c2e",
  "name": "Aria",
  "genesis_version": "standard-v1",
  "status": "alive"
}
```

Disk created under `{NLS_DATA_DIR}/agents/agent_7f3a9c2e/` including `knowledge.db`, `domain_tracker.json`, `config/*.json`, etc.

---

## Agent status

**Request**

```http
GET /agents/agent_7f3a9c2e
X-Runtime-Secret: ...
```

**Response** `200`

```json
{
  "agent_id": "agent_7f3a9c2e",
  "status": "alive",
  "name": "Aria",
  "turn_count": 42,
  "facts_in_memory": 128,
  "hormones": {
    "dopamine": 0.52,
    "cortisol": 0.18
  },
  "consciousness": "CONSCIOUS"
}
```

Fields vary by loaded subsystems; `404` if agent not in memory.

---

## Chat WebSocket (direct desktop / Electron)

**Connect**

```http
GET /ws/chat/agent_7f3a9c2e
Upgrade: websocket
X-Runtime-Secret: ...
```

**Client → server (user message)**

```json
{
  "type": "message",
  "content": "What did we decide about the API schema?",
  "session_key": "main"
}
```

**Server → client (streaming agentic events)**

```json
{ "type": "agentic_start", "run_id": "run-uuid" }
```

```json
{
  "type": "tool_call",
  "tool": "read",
  "args": { "path": "docs/api.md" }
}
```

```json
{
  "type": "token",
  "content": "We agreed to use "
}
```

```json
{
  "type": "response_end",
  "content": "We agreed to use OpenAPI 3.1 with nested examples.",
  "nls": {
    "signals_emitted": 1,
    "learned_facts": 0
  }
}
```

**Slash command**

```json
{
  "type": "command",
  "command": "sleep"
}
```

See [Chat slash commands](../../reference/chat-commands.md).

---

## Chat via NestJS relay (hosted web)

Browser does **not** open Python WS. Flow:

1. Socket.IO `join` → NestJS
2. Socket.IO `message` → NestJS `chat_request` on relay WS
3. Desktop `AgentRuntime.process_message_agentic_async`
4. NestJS emits Socket.IO `runtime` with `response_end`

Same JSON shapes on the final `runtime` event as Python `response_end`.

---

## OpenAI-compatible completion (per agent)

**Request**

```http
POST /v1/chat/completions
Content-Type: application/json
X-Runtime-Secret: ...

{
  "model": "agent_7f3a9c2e",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "stream": false
}
```

**Response** `200` (OpenAI shape)

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Hello! How can I help?" },
      "finish_reason": "stop"
    }
  ]
}
```

Uses configured inference URL (`NLS_VLLM_BASE_URL`), not local adapter weights.

---

## Force sleep (admin)

**Request**

```http
POST /admin/agents/agent_7f3a9c2e/sleep
X-Runtime-Secret: ...
```

**Response** `200`

```json
{
  "status": "queued",
  "agent_id": "agent_7f3a9c2e",
  "reason": "admin_requested"
}
```

---

## Related

- [Agent runtime](../agent-runtime.md)
- [Python API](../../reference/python-api.md)
- [Agentic loop sequence](../sequences/agentic-loop.md)
