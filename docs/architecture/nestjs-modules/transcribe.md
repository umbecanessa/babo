# Transcribe module

**Path:** `backend/src/transcribe/`

Upload audio from the web UI and forward to Python **Whisper** or GPU worker proxy.

---

## Files

| File | Role |
|------|------|
| `transcribe.module.ts` | `RuntimeModule`, `MulterModule` |
| `transcribe.controller.ts` | Single POST handler |

---

## HTTP

| Method | Path | Guard |
|--------|------|-------|
| POST | `/api/transcribe` | JWT |

- Field: `audio` (multipart, max 25MB)
- Calls `RuntimeService.transcribeAudio()`

---

## Python

`POST {RUNTIME_URL}/transcribe` — see [Transcribe & GPU worker](../../configuration/transcribe-and-gpu-worker.md).

Response includes `text`, `language`, `duration`, `backend`.

---

## Related

- [Runtime](runtime.md)
