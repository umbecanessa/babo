# Telegram integration

Connect a **Telegram bot** so users (and groups) can talk to your agent on Telegram.

**Skill name:** `telegram-channel`

Setup overview: [Integrations index](index.md) · architecture: [Channels & webhooks](../../architecture/channels-and-webhooks.md)

---

## Setup

Setup type: **conversational** — the agent walks you through:

1. Open Telegram, message **@BotFather**
2. Create a new bot (`/newbot`), copy the **HTTP API token**
3. Paste the token in Babo (Tools page or chat when prompted)
4. Babo registers the webhook via NestJS relay (desktop must be online for real-time delivery)

Fallback: long-polling on desktop when relay is unavailable.

---

## Configuration

| Field | Purpose |
|-------|---------|
| **Bot token** | From @BotFather |
| **DM policy** | `open`, `allowlist`, `disabled` |
| **Group policy** | Who may trigger replies in groups |
| **`require_mention`** | In groups/supergroups, reply only when `@your_bot` is mentioned (default for scoped fleets) |
| **`allow_from`** | Approved chat/user IDs |
| **Owner identity** | Your Telegram user id for owner linking |

Session keys: `telegram:dm:{user_id}` for DMs, `telegram:group:{chat_id}` for groups and supergroups.

---

## Groups & supergroups

| Scenario | Agent behavior |
|----------|----------------|
| **@mention** or reply-to-bot | Policy passes → reply routed through agentic inner loop |
| **No mention** | Message logged to **ambient** (`channel_ambient.jsonl`) only — no reply |
| **Bot added/removed** | `new_chat_member` system events are ignored (not chat turns) |

After removing and re-adding the bot, send a **new text message** with `@your_bot` — join/leave events alone do not trigger replies.

### Preemption

Direct Telegram messages (@mention, DM) **cancel daydream/DMN** immediately so vLLM is free for your reply. Background Job ticks and ambient-only group chatter do not preempt.

If Home chat is active at the same time, Telegram traffic is recorded in the **surface inbox** and steered into the Home copilot — see [Cross-surface defer](../../architecture/channels-and-webhooks.md#cross-surface-defer).

---

## Usage

- **DM** the bot for private agent chat
- **Group** — @mention the bot for team scenarios; tune `require_mention` and `allow_from`
- **Media** — photos, documents, voice, and video are saved to `workspace/uploads/`; voice is transcribed

Same memory as web chat — Telegram is another surface into the same brain.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Message in UI, no reply | Was it an @mention? Ambient-only messages do not reply. Check `runtime.log` for `agentic entry` vs `chat mode`. |
| No inbound at all | Desktop online? Relay connected? Webhook URL points at NestJS `/api/channels/webhook/telegram/{runtimeAgentId}`. |
| Slow or missing reply | vLLM busy (sleep, VLM restart, long job)? Retry @mention; check for `ChannelRelay ... failed to route` (120s timeout). |
| Wrong group id | Supergroups use `-100…` ids; regular groups use shorter negative ids — each is a separate session. |

---

## Related

- [Integrations overview](index.md)
- [Channels & webhooks](../../architecture/channels-and-webhooks.md)
- [Job, Trust & Squads](../job-trust-and-squads.md)
