# Filesystem module

**Path:** `backend/src/filesystem/`

JWT-protected proxy to the Python runtime **IDE filesystem API** (`/fs/*`).

---

## Files

| File | Role |
|------|------|
| `filesystem.module.ts` | Imports `RuntimeModule` |
| `filesystem.controller.ts` | Route handlers |
| `filesystem.service.ts` | `RuntimeService.proxyGet/Post` |

---

## HTTP routes

| Method | Path | Python |
|--------|------|--------|
| GET | `/api/fs/tree` | `/fs/tree` |
| GET | `/api/fs/read` | `/fs/read` |
| POST | `/api/fs/write` | `/fs/write` |
| POST | `/api/fs/edit` | `/fs/edit` |
| GET | `/api/fs/search` | `/fs/search` |
| GET | `/api/fs/readdir` | `/fs/readdir` |

All routes require JWT. Relay-aware when desktop connected.

---

## Python implementation

`server/routes/filesystem.py` uses `nls.engine.tools_builtin` (`FileReadTool`, `FileWriteTool`, `FileEditTool`) for path sandboxing.

---

## Related

- [Frontend application](../frontend-application.md) — IDE panel
- [Python API](../../reference/python-api.md)
