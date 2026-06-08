# Channels & webhooks

External messaging surfaces connect through **channel skills** and the **NestJS relay**.

Setup guides: [Integrations index](../guides/integrations/index.md).

---

## End-to-end path

```text
External provider (Telegram, Slack Events, Resend, Discord Gateway, …)
    → NestJS ingress (webhook or Gateway worker)
    → ChannelsService.pushToRelayByAgentId (if desktop online)
    → ChannelRelayClient receives channel_message
    → HTTP POST http://127.0.0.1:9222/skills/{channel}-channel/webhook/{runtimeAgentId}
    → Channel adapter: normalize → ambient log → PolicyEnforcer
    → process_channel_message (agentic inner-loop queue or direct dispatch)
    → ThalamicRouter: MICRO / FOCUS / DEEP engagement depth
    → Agentic response (or micro reply while deep work runs)
    → Outbound send via skill adapter (REST / Web API)
```

If desktop offline: payload stored in **PendingChannelMessage** (when agent exists in Postgres), drained on relay reconnect.

**Relay timeout:** local webhook POST uses a **120s** read timeout — agentic channel replies can exceed the old 30s limit.

---

## Inbound processing pipeline

Shared entry: `nls/skills/channel_processing.py` → `process_channel_message()`.

| Step | What happens |
|------|----------------|
| **Normalize** | Adapter maps provider payload → `session_key`, `content`, attachments, metadata |
| **Ambient log** | Group traffic recorded to `channel_ambient.jsonl` **before** policy (see below) |
| **Policy** | `should_respond()` — DM allowlist, group @mention, scoped channel enablement |
| **Preempt background** | `ConsciousnessScheduler.preempt_background()` cancels DMN/active dream (does **not** pause inner loop) |
| **Surface inbox** | Cross-surface messages recorded when Home chat is mid-turn |
| **Route** | Agentic path pushes `CHANNEL_MESSAGE` to inner loop; fallback uses direct foreground dispatch |

### Agentic vs chat fallback

When `agency.agentic_loop` is enabled and the inner loop (or model manager) is available, channels use the **inner-loop event queue** — the webhook returns immediately and the reply is sent asynchronously from `_dispatch_channel_event`.

Legacy **chat mode** (sync `process_message`) is only used when agentic is unavailable. It has a **120s** timeout and full `on_user_message()` pause semantics.

### Cross-surface defer

When the agent is busy on **Home** (`websocket:main`) and a message arrives on another surface (Telegram, Discord, …):

1. Message is recorded in **`surface_inbox.json`**
2. Text may be fed into the active Home copilot queue (`try_feed_active_copilot`)
3. **No parallel inner-loop dispatch** — avoids racing the foreground lock

The owner sees pending channel traffic in the UI inbox; the active Home turn can steer on it.

### Group ambient logging

Even when policy **rejects** a reply (no @mention in a scoped group), inbound group text is appended to:

```text
data/agents/{runtimeAgentId}/channel_ambient.jsonl
```

Each row includes `triggered: true|false` (whether a reply was attempted). Shared-group sessions inject a recent ambient snippet into the next **triggered** turn so the agent has context from side conversations.

Hooks: `nls/skills/channel_ambient.py` · persistence: `nls/runtime/channel_ambient.py`.

### Media attachments

Inbound photos, documents, voice, and video are downloaded to `workspace/uploads/` via `nls/skills/channel_attachments.py`. Voice notes are transcribed and prepended to the user turn. Outbound replies strip tool-call leaks before send (`sanitize_channel_outbound`).

Supported on Telegram, Discord, Slack, WhatsApp, and email (see bundled webhook handlers).

---

## Reply routing & preemption

One brain, layered concurrency — see [Execution slots](#execution-slots-micro-focus-deep).

| Trigger | Preempt behavior |
|---------|------------------|
| **Web chat** | `on_user_message()` — pause inner loop + cancel dream; resume after turn |
| **Channel @mention / DM / policy reply** | `preempt_background()` — cancel dream only; inner loop keeps breathing |
| **Ambient group (no reply)** | No preempt — background daydream / job ticks continue |

Direct channel messages (`user_direct: true` in event payload) are never **DEFER**'d behind daydreaming. When the deep slot is busy, the thalamic router assigns **FOCUS** → lightweight `micro_respond` on the channel while orchestration continues.

Frozen agents: channel preempt queues a consciousness **wake request** so the inner loop can dispatch on the next breath.

---

## Execution slots (micro / focus / deep)

**Source:** `nls/engine/execution_slots.py` · routing: `nls/engine/thalamic_router.py`

| Slot | Typical use | While deep busy |
|------|-------------|-----------------|
| **Micro** | Status query, greeting, proceed signal on channel | Runs concurrently |
| **Focus** | Direct @mention / DM needing a real reply | `micro_respond` or short agentic path |
| **Deep** | Full tool loop when idle | One at a time per agent |

The event queue drains when `is_user_busy` is false — background orchestration (`job_background`, drives) does **not** block channel dispatch.

Deferred events persist to `background_queue.jsonl` until the deep slot frees.

---

## Bundled channels

| Channel | Skill | Ingress | Outbound |
|---------|-------|---------|----------|
| WhatsApp | `whatsapp-channel` | Webhook + Baileys bridge | Baileys send |
| Telegram | `telegram-channel` | Webhook or long-poll | Bot API |
| Discord | `discord-channel` | NestJS Gateway WS → relay (+ local `discord.py` fallback) | Discord REST v10 |
| Slack | `slack-channel` | Events API → NestJS webhook (signed) → relay | Slack Web API |
| Email | `email-channel` | Resend inbound | Resend send API via NestJS |
| Google | `google-workspace` | OAuth APIs | Gmail/Calendar/Drive/Sheets |

---

## Channel scope

Discord and Slack use a shared reconciler (`nls/skills/channel_scope.py`) for **two-way** workspace channel sync.

### Concepts

| Term | Meaning |
|------|---------|
| **desired** (`enabled_desired`) | Owner enabled the channel in Babo (Tools UI or API) |
| **observed** (`platform_access`) | Bot/app can access the channel on the platform |
| **effective** (`effective_enabled`) | `desired ∧ observed` — only these receive inbound routing |
| **require_mention** | In shared channels, respond only when @mentioned |

`PolicyEnforcer` **groups** config is compiled from effective channels. Empty effective set → no guild/channel listening (safe default).

### Babo → platform

| Platform | When you enable a channel in Tools |
|----------|-----------------------------------|
| **Discord** | Permission overwrite push (needs Manage Roles or manual Discord setup) |
| **Slack** | `conversations.join` |

### Platform → Babo

| Platform | Trigger |
|----------|---------|
| **Discord** | Gateway `CHANNEL_CREATE`, `GUILD_CREATE`, manual **Sync** |
| **Slack** | `member_joined_channel`, `member_left_channel`, manual **Sync** |

Bulk sync after first connect **lists** channels without auto-enabling all of them. Auto-enable applies on invite/member events.

### Tools UI

When Discord or Slack is connected: **Tools → Integrations → [card] → Channel scope** panel with sync, enable toggles, and mention policy.

### Runtime API

```text
GET    /skills/discord-channel/channels/{runtimeAgentId}
POST   /skills/discord-channel/channels/{runtimeAgentId}/sync
PATCH  /skills/discord-channel/channels/{runtimeAgentId}/{channelId}

GET    /skills/slack-channel/channels/{runtimeAgentId}
POST   /skills/slack-channel/channels/{runtimeAgentId}/sync
PATCH  /skills/slack-channel/channels/{runtimeAgentId}/{channelId}
```

---

## Discord Gateway (NestJS)

Discord has no Telegram-style HTTP webhook for message events. NestJS runs **`DiscordGatewayService`**:

1. Sidecar: `POST /api/channels/discord/register/{runtimeAgentId}` with bot token
2. Service opens Discord Gateway WebSocket, waits for **`READY`** (20s timeout)
3. Forwards `MESSAGE_CREATE`, `CHANNEL_CREATE`, `CHANNEL_UPDATE`, `GUILD_CREATE` via relay
4. Sidecar shutdown: `POST /api/channels/discord/unregister/{runtimeAgentId}`

If registration fails or times out, desktop may start a **local `discord.py` Gateway** instead.

---

## Slack Events API (NestJS)

1. Slack app Event Subscriptions → Request URL:

   ```text
   https://<nestjs>/api/channels/webhook/slack/{runtimeAgentId}
   ```

2. Sidecar registers signing secret: `POST /api/channels/slack/register/{runtimeAgentId}`
3. NestJS verifies `X-Slack-Signature` on raw body before relay
4. `url_verification` challenge handled at controller (no signature required)

Python skill webhook verifies signatures when Slack headers are present on direct hits.

---

## Webhook registration (Telegram example)

1. User completes @BotFather setup in Tools UI
2. Adapter calls `register_webhook_relay(nestjs_base, agent_id)`
3. Telegram API webhook URL = `{nestjs}/api/channels/webhook/telegram/{runtimeAgentId}`
4. Desktop must be online (relay) to receive events in real time

Fallback: long-polling on desktop when relay unavailable.

---

## Outbound guard (Discord & Slack)

When the channel skill is **enabled**, `discord_send` / `slack_send` only target:

- Effective scoped channel IDs
- Known inbound senders
- Contacts (`discord_id` / `slack_id`)
- `owner_identity` and `allow_from`

Prevents accidental sends to arbitrary IDs. Legacy JSON `discord` / `slack` tools are hidden when bundled channel skills are enabled.

---

## Email special case

- **Activate:** `POST /api/channels/email/activate/:agentId` (JWT) — creates alias
- **Send:** `POST /api/channels/email/send` (`X-Runtime-Secret`) — Python requests outbound send through Resend

Inbound: Resend webhook → NestJS → relay → email-channel skill.

---

## DM policies

Per-skill config:

- `open` — respond to anyone
- `allowlist` — approved contacts only
- `disabled` — ignore inbound

Owner identity fields link channels to the logged-in user.

---

## Shared memory

All channels feed the **same AgentRuntime** — WhatsApp and web chat share Cryptex and WM. Chat UI groups threads by channel (including Discord and Slack) with human-readable labels when platform metadata is available (`channel_name`, guild title).

Agent tool **`channel_history`** reads persisted session + ambient logs for a channel thread (lead/squad scenarios).

---

## Python `/webhooks` vs NestJS `/api/channels/webhook`

| Ingress | Path | When |
|---------|------|------|
| **NestJS (production)** | `/api/channels/webhook/{channel}/{runtimeAgentId}` | Slack, Telegram, email; requires desktop relay for real-time |
| **NestJS Gateway** | `/api/channels/discord/register/{runtimeAgentId}` | Discord message ingress |
| **Python direct** | `/webhooks/telegram/{agent_id}` | Self-hosted stacks exposing port 9222 |
| **Skill-mounted** | `/skills/{channel}-channel/webhook/{agent_id}` | Relay target; Baileys WhatsApp bridge |

Prefer NestJS for cloud deployments so pending messages queue when desktop is offline.

---

## Security

- Webhook URLs contain `runtimeAgentId` — use unguessable ids
- Configure `RUNTIME_SHARED_SECRET` on relay and runtime hooks
- Slack signing secrets verified at NestJS before relay
- Discord bot tokens only sent to NestJS over `X-Runtime-Secret`
- Only connect trusted MCP servers

---

## Related

- [Discord integration](../guides/integrations/discord.md)
- [Slack integration](../guides/integrations/slack.md)
- [Relay protocol](../reference/relay-protocol.md)
- [Skills system](skills-system.md)
- [NestJS backend](nestjs-backend.md)
- [Add a channel integration](../extension/add-channel-integration.md)
