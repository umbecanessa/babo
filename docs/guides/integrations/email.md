# Email inbox integration

Give your agent its own **email address** for threaded conversations and content ingestion.

**Skill name:** `email-channel`

---

## Setup

Setup type: **auto** — after enabling the skill, Babo provisions an address via the configured email provider (Resend API backend).

You see a confirmation: *"Your agent now has a personal email address!"*

---

## Capabilities

- **Send and receive** email as the agent identity
- **Threaded conversations** — replies stay in context
- **Content ingestion** — forward newsletters or documents; agent routes to study pipeline
- **Email ledger** — `email_history` tool queries sent/received log

---

## vs Google Workspace Gmail

| | **Email channel** | **Google Workspace** |
|--|-------------------|----------------------|
| Address | `@inbox.*` agent alias | Your Gmail |
| Persona | Agent speaks as itself | Agent acts on your behalf |

---

## Usage examples

- Put the agent address on a contact form
- Forward travel confirmations — agent adds to memory/calendar skills
- Email the agent tasks while away from Babo UI

---

## Related

- [Integrations overview](index.md)
- [Google Workspace](google-workspace.md)
