# Babo Admin

Operator console for Babo Cloud and self-hosted deployments. Glassmorphic Angular UI for users, agents, and inference token usage.

## Prerequisites

- Node.js 22+
- NestJS backend running (`backend/`) with Postgres migrated
- CORS allowing the admin origin (default dev: `http://localhost:4201`)

## Development

```bash
cd admin
npm install
npm start -- --port 4201
```

Set API URL if not localhost:

```html
<!-- admin/src/index.html before boot -->
<script>window.__BABO_ADMIN_API__ = 'https://api.babo.agency/api';</script>
```

Or edit `src/environments/environment.ts`.

## First-run setup

1. Open the admin URL. If **no administrator** exists, you are sent to **Setup**.
2. **Fresh database:** create a new admin email and password.
3. **Existing users (e.g. cloud already live):** use **Claim admin** — sign in with your existing Babo email/password; that account is promoted to `admin` (only while no admin exists).
4. Sign in at **Login** — only accounts with `role: admin` are accepted (`POST /api/auth/admin/login`).

Regular Babo user accounts cannot access this console. Promote additional admins from **Users** after the first operator is configured.

If an admin already exists, use **Login** or promote via **Users → Promote** (requires an existing admin session).

## Production / Railway

Deploy from the `admin/` root (uses `Dockerfile` + `railway.json`).

Set on the Railway service (optional — defaults to Babo Cloud API):

```env
BABO_ADMIN_API=https://api.babo.agency/api
```

The container injects this into `index.html` at startup so API calls go to NestJS, not the admin static host.

Ensure NestJS CORS allows your admin origin (e.g. `https://admin-production-d73d.up.railway.app`).

## Production build (manual)

```bash
npm run build
```

Static output in `admin/dist/admin/browser/`. Serve behind nginx or nest static module; point `__BABO_ADMIN_API__` at your Nest `/api` prefix.

## API (admin JWT required unless noted)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/setup/status` | Public — `{ needsSetup, hasAdmin, userCount, canClaimExisting, setupMode }` |
| POST | `/api/admin/setup` | Public (once) — bootstrap first admin |
| GET | `/api/admin/stats` | Dashboard counts (users, agents, API keys) |
| GET | `/api/admin/users` | All users |
| GET | `/api/admin/users/:id/usage` | Per-user token ledger |
| GET | `/api/admin/usage` | Fleet token usage summary |
| GET | `/api/admin/agents` | All agents cross-user |

See `docs/reference/nestjs-api.md` for the full admin surface.
