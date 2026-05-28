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

1. Start backend with empty `users` table (or no admin role).
2. Open `http://localhost:4201` — you are redirected to **Setup**.
3. Create the first admin account (`POST /api/admin/setup`).
4. Sign in and use Dashboard / Users / Agents / Token usage.

If an admin already exists, use **Login** with an admin account (or promote via SQL: `UPDATE users SET role = 'admin' WHERE email = '...'`).

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
| GET | `/api/admin/setup/status` | Public — `{ needsSetup, hasAdmin, userCount }` |
| POST | `/api/admin/setup` | Public (once) — bootstrap first admin |
| GET | `/api/admin/stats` | Dashboard counts + runtime health |
| GET | `/api/admin/users` | All users |
| GET | `/api/admin/users/:id/usage` | Per-user token ledger |
| GET | `/api/admin/usage` | Fleet token usage summary |
| GET | `/api/admin/agents` | All agents cross-user |

See `docs/reference/nestjs-api.md` for the full admin surface.
