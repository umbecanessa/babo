# NestJS Agents API — request/response examples

Base URL: `https://<api-host>/api`  
Auth: `Authorization: Bearer <access_token>` unless noted.

`agentId` in paths is the **Postgres UUID**. Python uses `runtimeAgentId` from responses.

---

## Create agent

**Request**

```http
POST /api/agents
Content-Type: application/json
Authorization: Bearer eyJhbG...

{
  "genesisVersion": "standard-v1",
  "name": "Aria",
  "sovereignty": "local"
}
```

**Response** `201` (shape from `AgentsService.create`)

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "userId": "user-uuid",
  "runtimeAgentId": "agent_7f3a9c2e",
  "name": "Aria",
  "genesisVersion": "standard-v1",
  "status": "alive",
  "createdAt": "2026-05-23T10:00:00.000Z",
  "runtime": {
    "agent_id": "agent_7f3a9c2e",
    "name": "Aria",
    "genesis_version": "standard-v1",
    "status": "alive"
  }
}
```

**Python side** (`POST /agents` via `RuntimeService.createAgent`):

```json
{
  "genesis_version": "standard-v1",
  "name": "Aria",
  "sovereignty": "local",
  "ownerEmail": "you@example.com",
  "ownerName": "You"
}
```

---

## List agents (with live runtime)

**Request**

```http
GET /api/agents
Authorization: Bearer eyJhbG...
```

**Response** `200`

```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "runtimeAgentId": "agent_7f3a9c2e",
    "name": "Aria",
    "status": "alive",
    "runtime": {
      "agent_id": "agent_7f3a9c2e",
      "status": "alive",
      "turn_count": 42,
      "facts_in_memory": 128
    }
  }
]
```

If desktop offline, `runtime` may be `{ "status": "unreachable" }`.

---

## Desktop sync (register existing runtime agent)

**Request**

```http
POST /api/agents/sync
Authorization: Bearer eyJhbG...

{
  "runtimeAgentId": "agent_7f3a9c2e",
  "name": "Aria",
  "genesisVersion": "standard-v1"
}
```

**Response** `200`

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "runtimeAgentId": "agent_7f3a9c2e",
  "name": "Aria",
  "created": false
}
```

`created: true` when a new Postgres row was inserted.

---

## Relay status

**Request**

```http
GET /api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/relay-status
Authorization: Bearer eyJhbG...
```

**Response** `200`

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "runtimeAgentId": "agent_7f3a9c2e",
  "online": true
}
```

`online` is true when desktop has an open relay WebSocket.

---

## Proxy: chain summary

**Request**

```http
GET /api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chain
Authorization: Bearer eyJhbG...
```

**Response** `200` (passthrough from Python `GET /admin/agents/{runtimeAgentId}/chain`)

```json
{
  "height": 42,
  "head_hash": "abc123...",
  "genesis_version": "standard-v1",
  "block_count": 42
}
```

Exact fields depend on runtime version; errors: `403` (not owner), `502` (runtime down, no relay).

---

## Acquire device lease

**Request**

```http
POST /api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/lease/acquire
Authorization: Bearer eyJhbG...

{
  "deviceId": "desktop-install-uuid",
  "clientName": "Babo Desktop"
}
```

**Response** `200`

```json
{
  "leaseId": "lease-uuid",
  "agentId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "deviceId": "desktop-install-uuid",
  "expiresAt": "2026-05-23T11:00:00.000Z"
}
```

**Conflict** `409` — another device holds the lease.

See [Device lease](../device-lease.md).

---

## Error patterns

| Status | Meaning |
|--------|---------|
| `401` | Missing/invalid JWT |
| `403` | Agent belongs to another user |
| `404` | Unknown Postgres `agentId` |
| `502` | `RUNTIME_URL` unreachable and no relay |

---

## Related

- [Agents module](../nestjs-modules/agents.md)
- [NestJS API reference](../../reference/nestjs-api.md)
- [Relay join sequence](../sequences/relay-join.md)
