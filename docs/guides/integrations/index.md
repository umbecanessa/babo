# Integrations overview

Babo ships bundled **skills** for channels and extensibility. Install from **Tools → Integrations** or the community search section.

---

## Channel integrations

| Skill | What your agent can do | Setup |
|-------|------------------------|-------|
| [WhatsApp](whatsapp.md) | Send/receive via your personal account | QR pairing |
| [Telegram](telegram.md) | Bot DMs and groups | @BotFather token |
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

---

## Contacts tool

Once channels are connected, the **contacts** tool unifies:

- WhatsApp known senders
- Telegram users
- Email correspondents
- Manual entries you add

Actions: `search`, `list`, `groups`, `recent`, `add`, `edit`, `delete`, `owner`.

---

## Webhooks & bridges

Some skills use **sidecar bridges** (e.g. WhatsApp Baileys on port 9223). Babo manages process lifecycle via `SkillBridge` — health checks, restart, logs.

Inbound messages hit webhook routes and forward into the agentic loop as channel events.

---

## Related

- [Tools & skills](../tools-and-skills.md)
- [Chat](../chat.md)
