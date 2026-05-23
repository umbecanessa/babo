# Google Workspace integration

Give your agent access to **Gmail, Google Calendar, Google Drive, and Google Sheets** via OAuth.

**Skill name:** `google-workspace`

---

## Setup

Setup type: **ui** — no manual Google Cloud project needed (Babo ships app credentials).

1. Open **Tools → Integrations → Google Workspace**
2. Click **Connect** — OAuth modal opens
3. Sign in with your Google account
4. Grant requested scopes
5. Babo confirms connection status

In chat, the agent can call `google_workspace_connect(action='connect')` to open the same modal.

---

## Per-service access

Configure each category independently:

| Service | Access levels |
|---------|---------------|
| **Gmail** | read/write, read only, disabled |
| **Calendar** | read/write, read only, disabled |
| **Drive** | read only (default), disabled |
| **Sheets** | read/write, read only, disabled |

**Require confirmation** flag — agent asks before destructive writes.

---

## Tools

Skill exposes tools such as:

- Gmail read/send/search
- Calendar list/create/update events
- Drive file listing and download
- Sheets read/write ranges

Exact tool names appear in **Tools → Agent tools** after connection.

---

## Email channel vs Gmail

| | **Email channel** | **Google Workspace (Gmail)** |
|--|-------------------|------------------------------|
| Identity | Agent's own inbox address | **Your** Gmail account |
| Use case | Agent persona email | Read/send as you |

Many users enable both.

---

## Related

- [Integrations overview](index.md)
- [Email inbox](email.md)
