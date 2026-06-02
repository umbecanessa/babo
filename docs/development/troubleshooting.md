# Troubleshooting

Common issues when running Babo locally or in production.

---

## Backend (NestJS)

### `nest build` fails

Fix TypeScript errors in `backend/src/`. Recent example: duplicate keys in object literals (`chat.gateway.ts`).

```bash
cd backend && npm install && npx nest build
```

### Railway skips backend deploy

Railway only rebuilds when files under the service root change. Touch `backend/README.md` or any `backend/src/**` file and push.

### Database connection errors

- Confirm `DATABASE_URL` is set
- Run `npx prisma migrate deploy` against that database
- Postgres plugin must be in the same Railway project or network-accessible

---

## Web UI + relay (remote browser)

### “Agent desktop is not connected”

The hosted web UI needs an **active relay** from the desktop runtime.

Checklist:

1. Desktop app is running and past setup wizard.
2. Python runtime started (port 9222).
3. Desktop **Backend URL** matches your NestJS URL.
4. `NESTJS_URL` is passed to Python (desktop `config-manager.ts` sets this).
5. Dashboard shows agent **online** (`relay-status` API).

### Chat works in desktop but not in browser

| Desktop | Browser |
|---------|---------|
| Direct WS → `127.0.0.1:9222` | Socket.IO → NestJS → relay |

They are different code paths. Fix relay connectivity, not only local WS.

### `/api/rt/...` returns 502

`RuntimeProxyController` could not complete `proxyHttpViaRelay`. Desktop offline or relay timed out (default 30s).

---

## Python runtime

### `curl http://127.0.0.1:9222/health` fails

- Start uvicorn: `uvicorn server.main:app --host 127.0.0.1 --port 9222`
- Check port conflict with desktop `runtimePort`
- Review desktop logs / Electron devtools

### Inference errors

- Verify `NLS_VLLM_BASE_URL` and `NLS_HF_MODEL`
- Test provider with curl to `/v1/chat/completions`
- Set `NLS_INFERENCE_API_KEY` if required

### Relay never connects

- `websockets` package installed (`requirements-desktop.txt`)
- `NESTJS_URL` reachable from desktop machine (HTTPS/WSS)
- `NLS_SHARED_SECRET` matches NestJS `RUNTIME_SHARED_SECRET` when secret auth enabled

---

## Desktop build

### `npx ng build` fails in `build-local.ps1`

Install frontend deps first:

```bash
cd frontend && npm install
```

### Missing `build/icon.ico`

Run `desktop/build-local.ps1` — it copies icons from `frontend/src/assets/images/babo.png`.

---

## Tests

```bash
export NLS_PRODUCT_MODE=1
python -m pytest tests/ -q
```

---

## Related

- [Deployment topologies](../architecture/deployment-topologies.md)
- [Cloud deployment](../configuration/cloud-deployment.md)
- [Local development](local-development.md)
