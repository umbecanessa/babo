# Transcribe & GPU worker

Voice input and some vision/embedding tools can offload heavy models to an optional **GPU worker** service.

---

## Speech-to-text (`POST /transcribe`)

**Source:** `server/routes/transcribe.py`

| Step | Behavior |
|------|----------|
| 1 | If `NLS_GPU_WORKER_URL` set → proxy audio to worker |
| 2 | Else lazy-load local Whisper (`openai-whisper` or `faster-whisper`) |
| 3 | Return `{ text, language, duration, backend }` |

Multipart body: field `audio` (file upload).

Auth: same as runtime (`X-Runtime-Secret` or API key) — router mounted with `verify_auth` in `server/main.py`.

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NLS_GPU_WORKER_URL` | Base URL of remote worker (e.g. `https://gpu.example.com`) |
| `NLS_GPU_WORKER_SECRET` | Sent as `X-GPU-Worker-Secret` on proxy requests |
| `NLS_IS_GPU_WORKER` | When `1`, this process serves `/transcribe` locally with Whisper |

Desktop without CUDA often sets only `NLS_GPU_WORKER_URL` so transcription stays fast without bundling Whisper in the Electron venv.

---

## GPU worker endpoints (remote)

The runtime proxies to:

```http
POST {NLS_GPU_WORKER_URL}/transcribe
X-GPU-Worker-Secret: <secret>
```

Other agent tools (semantic search, visual cortex) may also call the worker when configured — see `nls/tools/visual_model.py`, `nls/tools/agent_tools/semantic_search.py`.

---

## Local Whisper backends

Tried in order on the machine running transcription:

1. `openai-whisper` (PyTorch, CUDA if available)
2. `faster-whisper` with CUDA
3. `faster-whisper` on CPU

If none install and no GPU worker URL → `503` with hint to set `NLS_GPU_WORKER_URL` or install `faster-whisper`.

---

## Product note

GPU worker is **optional infrastructure**, not a hosted Babo service. Operators run their own worker container or VM; the open-source repo documents the contract only.

---

## Related

- [Environment (complete)](../reference/environment-complete.md)
- [Python API](../reference/python-api.md)
- [Tools system](../architecture/tools-system.md)
