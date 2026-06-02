# Local development

Run Babo from source for contribution or customization.

---

## Clone

```bash
git clone https://github.com/umbecanessa/babo.git
cd babo
```

---

## Python runtime

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-desktop.txt
pip install -r requirements.txt   # if developing server-only features

export NLS_VLLM_BASE_URL=http://127.0.0.1:11434/v1
export NLS_HF_MODEL=llama3.2

uvicorn server.main:app --reload --host 127.0.0.1 --port 9222
```

---

## Backend

```bash
docker compose up -d postgres
cd backend
cp .env.example .env
npm install
npx prisma migrate dev
npm run start:dev
```

---

## Frontend

```bash
cd frontend
npm install
npm start
```

Open http://localhost:4200 — uses `environment.ts` pointing at `localhost:3000`.

---

## Desktop (optional)

```bash
cd frontend && npm install
cd ../desktop
npm install
npm run start:dev
```

See `desktop/package.json` (`start:dev` runs Electron with `NODE_ENV=development`).

---

## Tests

```bash
export NLS_PRODUCT_MODE=1
python -m pytest tests/ -q
```

---

## Project layout tips

| Change | Look in |
|--------|---------|
| UI feature | `frontend/src/app/features/` |
| API route | `backend/src/` |
| Agent tool | `nls/tools/agent_tools/` |
| Bundled skill | `nls/skills/bundled/` |
| Loop behavior | `nls/agentic/` |
| Memory | `nls/brain/` |
| HTTP/WS API | `server/routes/` |

---

## Related

- [Contributing](https://github.com/umbecanessa/babo/blob/main/CONTRIBUTING.md)
- [Configuration](../configuration/index.md)
