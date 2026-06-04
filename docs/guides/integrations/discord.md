# Discord integration

Connect a **Discord bot** so users can talk to your agent in scoped guild channels and DMs.

**Skill name:** `discord-channel`

---

## What you get

- Two-way messaging in **guild text channels** you explicitly enable
- **DMs** with policy controls (`open`, `allowlist`, `disabled`)
- **Channel scope UI** in Tools — list channels, toggle listening, sync from Discord
- **Contacts** integration — store `discord_id` for outbound targeting
- Same brain as web chat — Discord is another session into Cryptex

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Discord bot token** | From [Discord Developer Portal](https://discord.com/developers/applications) |
| **NestJS reachable over HTTPS** (Babo Cloud / self-hosted) | Gateway worker runs on NestJS; events relay to desktop |
| **Babo Desktop online** | Relay WebSocket delivers inbound events in real time |
| **Bot invited to your server** | Guild channels appear after sync |

Local-only stacks without NestJS can fall back to a **local `discord.py` Gateway** on the desktop sidecar when NestJS registration fails or `NESTJS_URL` is unset.

---

## Setup

### 1. Create the bot (Discord Developer Portal)

1. **Applications → New Application**
2. **Bot → Add Bot** → copy the **token**
3. Enable **Message Content Intent** (required for reading message text)
4. Under **OAuth2 → URL Generator**, select scopes `bot` and permissions:
   - View Channels
   - Send Messages
   - Read Message History
5. Use the generated invite URL to add the bot to your server

### 2. Connect in Babo

**Tools → Integrations → Discord → Setup in Chat**, or paste the token when prompted and call `discord_setup`.

The setup tool validates the token, stores config per agent, and registers the NestJS Gateway when relay is configured.

### 3. Configure policy and scope

After connect, open the **Discord** integration card again:

| Area | Purpose |
|------|---------|
| **Config form** | `owner_identity`, DM policy, `allow_from`, relay URL |
| **Channel scope panel** | Enable channels, `@mention only` toggle, **Sync channels** |

Or ask the agent to run `skill_configure(skill_name='discord-channel', ...)`.

---

## Channel scope (two-way sync)

Discord and Babo maintain **desired** vs **observed** channel access:

| Field | Meaning |
|-------|---------|
| `enabled_desired` | You turned the channel on in Babo Tools |
| `platform_access` | Bot can see the channel in Discord (invite / permissions) |
| `effective_enabled` | Both true — bot listens and replies here |
| `require_mention` | In guild channels, only respond when @mentioned (default: on) |

**Effective routing** = `enabled_desired ∧ platform_access`.

### Sync behavior

| Event | What happens |
|-------|----------------|
| **Sync channels** (Tools UI or API) | Fetches guild channels from Discord API; updates observed list |
| **Bot invited to new channel** | Gateway `CHANNEL_CREATE` / `GUILD_CREATE` → auto-enable that channel |
| **First bulk sync after setup** | Channels appear in the list but stay **off** until you enable them (avoids enabling every guild channel at once) |

### Discord permission push

When you **enable** a channel in Tools, Babo tries to push a permission overwrite so the bot can view/send in that channel. If the bot lacks **Manage Roles**, you may see a warning — scope is still saved in Babo; fix permissions manually in Discord.

---

## Message flow

```text
Discord Gateway (NestJS DiscordGatewayService)
    → channel_message on relay WS
    → POST http://127.0.0.1:9222/skills/discord-channel/webhook/{runtimeAgentId}
    → PolicyEnforcer + channel scope check
    → Agentic loop
    → discord_send / adapter REST outbound
```

**Fallback:** if NestJS Gateway registration fails or never reaches `READY`, desktop starts a local `discord.py` Gateway.

**Shutdown:** when the skill stops, desktop calls `POST /api/channels/discord/unregister/{agentId}` to tear down the NestJS session.

---

## NestJS registration

Not a public webhook URL — the Python sidecar registers the bot token:

```http
POST /api/channels/discord/register/{runtimeAgentId}
X-Runtime-Secret: <RUNTIME_SHARED_SECRET>
Content-Type: application/json

{ "bot_token": "..." }
```

Response includes `ready: true` only after the Gateway receives Discord `READY` (20s timeout).

---

## Agent tools

| Tool | Purpose |
|------|---------|
| `discord_setup` | Validate token during onboarding |
| `discord_send` | Outbound text/files (`channel_id`, optional `reply_to_message_id`) |
| `contacts` | `discord_id` field for first-contact outbound |
| `skill_configure` | Owner, DM policy, allowlist |

### Outbound guard

When the skill is **enabled**, the agent can only send to:

- Effective scoped channel IDs
- Known senders (inbound history)
- Contacts with `discord_id`
- `owner_identity` / `allow_from`

This prevents accidental broadcast to arbitrary snowflake IDs.

---

## Channel scope API (runtime)

Base: `http://127.0.0.1:9222/skills/discord-channel/` (desktop) — `{agentId}` is **runtimeAgentId**.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/channels/{agentId}` | List scoped channels (syncs first) |
| POST | `/channels/{agentId}/sync` | Refresh from Discord |
| PATCH | `/channels/{agentId}/{channelId}` | `{ "enabled": true, "require_mention": true }` |
| GET | `/status/{agentId}` | Connection + scope summary |

---

## Legacy `discord` JSON tool

The older **`discord`** tool in `nls/config/tools/discord.json` (webhook/bot HTTP executor) is **deprecated**. When `discord-channel` is enabled for an agent, the legacy tool is hidden from the Tools list. Use `discord_send` instead.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No inbound messages | Desktop relay online? NestJS Gateway registered (`ready: true`)? Channel **effective enabled**? |
| Bot ignores guild messages | `@mention only` may be on — mention the bot or disable in scope panel |
| Permission warning on enable | Grant bot **Manage Roles** or set channel permissions manually |
| Duplicate messages | Avoid running both NestJS Gateway and local Gateway — registration success stops local fallback |

---

## Related

- [Integrations overview](index.md)
- [Slack](slack.md)
- [Channels & webhooks](../../architecture/channels-and-webhooks.md)
- [NestJS Channels API examples](../../architecture/examples/nestjs-channels-api.md)
