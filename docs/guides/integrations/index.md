# Integrations overview

Babo ships bundled **skills** for channels and extensibility. Install from **Tools → Integrations** or the community search section.

---

## Channel integrations

| Skill | What your agent can do | Setup |
|-------|------------------------|-------|
| [WhatsApp](whatsapp.md) | Send/receive via your personal account | QR pairing |
| [Telegram](telegram.md) | Bot DMs and groups | @BotFather token |
| [Discord](discord.md) | Bot in scoped guild channels + DMs | Developer Portal bot token |
| [Slack](slack.md) | App in scoped workspace channels + DMs | Slack app token + signing secret |
| [Google Workspace](google-workspace.md) | Gmail, Calendar, Drive, Sheets | OAuth modal |
| [Email inbox](email.md) | Dedicated agent email address | Automatic |

All channels share the same agent memory — a WhatsApp message and a web chat message see the same brain.

---

## Extensibility

| System | What it adds | Setup |
|--------|--------------|-------|
| [MCP servers](mcp.md) | Any MCP-compatible tools | Command or URL |
| [ClawHub](clawhub.md) | Community skill packages | Search + install |

---

## Policies

Channel skills support **DM policies**:

- `open` — respond to anyone
- `allowlist` — only approved contacts
- `disabled` — inbound ignored

Configure per skill after setup. **Owner identity** fields tie channels to your account.

### Channel scope (Discord & Slack)

Guild/workspace channels require explicit **scope** before the bot responds:

1. Connect the integration (bot token / Slack app credentials)
2. Open **Tools → Integrations → Discord** or **Slack** when connected
3. Use the **Channel scope** panel: **Sync channels**, toggle **Enabled**, set **@mention only**
4. Or use the runtime API (`GET/PATCH /skills/{channel}-channel/channels/...`)

**Effective enabled** = you enabled it in Babo **and** the bot has platform access (invited / member). See [Discord](discord.md#channel-scope-two-way-sync) and [Slack](slack.md#channel-scope-two-way-sync).

Telegram and WhatsApp use group/DM policies without the same scoped-channel matrix.

---

## Contacts tool

Once channels are connected, the **contacts** tool unifies:

- WhatsApp known senders
- Telegram users
- Discord and Slack users (via `discord_id` / `slack_id`)
- Email correspondents
- Manual entries you add
- Any future channel skill that registers with the contacts plugin API

Actions: `search`, `list`, `groups`, `recent`, `add`, `edit`, `delete`, `owner`.

---

## Webhooks & bridges

Some skills use **sidecar bridges** (e.g. WhatsApp Baileys on port 9223). Babo manages process lifecycle via `SkillBridge` — health checks, restart, logs.

| Channel | Ingress path |
|---------|----------------|
| Telegram, Slack, email | HTTP webhook → NestJS → relay |
| Discord | NestJS **Gateway WebSocket** → relay (no HTTP message webhook) |
| WhatsApp | Local bridge → skill webhook |

Inbound messages hit webhook routes (or Gateway events) and forward into the agentic loop as channel events.

**Keep Babo Desktop online** for real-time delivery. NestJS queues pending messages when the relay is down (cloud agents with Postgres records).

---

## Related

- [Tools & skills](../tools-and-skills.md)
- [Chat](../chat.md)
