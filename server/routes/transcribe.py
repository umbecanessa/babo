"""Transcription endpoint -- Speech-to-Text via Whisper.

When running on a GPU host, uses a local Whisper model (OpenAI or
faster-whisper).  When running on a desktop client without Whisper installed,
automatically proxies the request to the remote GPU Worker service.

The model is lazy-loaded on first request to avoid slowing down server startup.

Endpoint::

    POST /transcribe
        Body: multipart/form-data with "audio" file field
        Returns: {"text": "transcribed text", "language": "en", "duration": 3.2}
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter(tags=["transcribe"])

# Lazy-loaded Whisper model (singleton)
_whisper_model: Any = None
_whisper_lock: Any = None
_whisper_backend: str = ""


def _get_whisper_model() -> tuple[Any, str]:
    """Lazy-load a Whisper model on first use.

    Tries backends in order:
      1. openai-whisper (PyTorch) -- works on aarch64 with CUDA
      2. faster-whisper (CTranslate2) with CUDA
      3. faster-whisper (CTranslate2) on CPU
    """
    global _whisper_model, _whisper_lock, _whisper_backend

    if _whisper_lock is None:
        import threading
        _whisper_lock = threading.Lock()

    if _whisper_model is not None:
        return _whisper_model, _whisper_backend

    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model, _whisper_backend

        t0 = time.perf_counter()

        # --- Try 1: OpenAI Whisper (PyTorch, native CUDA on any arch) ---
        try:
            import torch
            import whisper as openai_whisper

            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(
                "Loading OpenAI Whisper model (small) on %s...", device,
            )
            _whisper_model = openai_whisper.load_model("small", device=device)
            _whisper_backend = "openai-whisper"
            elapsed = time.perf_counter() - t0
            logger.info(
                "OpenAI Whisper loaded in %.1fs (device=%s)", elapsed, device,
            )
            return _whisper_model, _whisper_backend
        except ImportError as exc:
            logger.info("openai-whisper not installed (%s), trying faster-whisper", exc)
        except Exception as exc:
            logger.warning("OpenAI Whisper failed: %s — trying faster-whisper", exc, exc_info=True)

        # --- Try 2: faster-whisper with CUDA ---
        try:
            import ctranslate2
            cuda_ok = ctranslate2.get_cuda_device_count() > 0
        except ImportError as exc:
            logger.info("ctranslate2 not installed (%s), faster-whisper will use CPU", exc)
            cuda_ok = False
        except Exception as exc:
            logger.warning("ctranslate2 probe failed: %s", exc)
            cuda_ok = False

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError(
                f"faster-whisper not installed: {exc}. "
                "Run: pip install faster-whisper"
            ) from exc

        if cuda_ok:
            logger.info("Loading faster-whisper (small) on CUDA...")
            _whisper_model = WhisperModel(
                "small", device="cuda", compute_type="float16",
            )
            _whisper_backend = "faster-whisper-cuda"
        else:
            logger.warning(
                "ctranslate2 has no CUDA -- loading faster-whisper on CPU"
            )
            _whisper_model = WhisperModel(
                "small", device="cpu", compute_type="int8",
            )
            _whisper_backend = "faster-whisper-cpu"

        elapsed = time.perf_counter() - t0
        logger.info("Whisper loaded in %.1fs (backend=%s)", elapsed, _whisper_backend)
        return _whisper_model, _whisper_backend


async def _proxy_to_gpu_worker(
    audio_bytes: bytes, filename: str, content_type: str,
    request: Request | None = None,
) -> dict:
    """Forward transcription to the remote GPU Worker."""
    import httpx

    gpu_url = (
        os.environ.get("NLS_TRANSCRIBE_WORKER_URL", "")
        or os.environ.get("NLS_GPU_WORKER_URL", "")
    )
    gpu_secret = (
        os.environ.get("NLS_TRANSCRIBE_WORKER_SECRET", "")
        or os.environ.get("NLS_GPU_WORKER_SECRET", "")
    )

    if not gpu_url:
        try:
            from server.config import get_settings
            settings = get_settings()
            gpu_url = gpu_url or settings.gpu_worker_url
            gpu_secret = gpu_secret or settings.gpu_worker_secret
        except Exception:
            pass

    # Auto-discover from genesis_sync (desktop/remote mode)
    if not gpu_url and request is not None:
        gs = getattr(getattr(request, "app", None), "state", None)
        gs = getattr(gs, "genesis_sync", None) if gs else None
        if gs is not None:
            gpu_url = getattr(gs, "gpu_worker_url", "")
            gpu_secret = gpu_secret or getattr(gs, "secret", "")

    if not gpu_url:
        raise HTTPException(
            500,
            "No local Whisper and no GPU Worker URL configured. "
            "Set NLS_GPU_WORKER_URL or install faster-whisper.",
        )

    url = f"{gpu_url.rstrip('/')}/transcribe"
    headers = {}
    if gpu_secret:
        headers["X-GPU-Worker-Secret"] = gpu_secret

    logger.info("Proxying transcription to GPU Worker at %s", url)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            url,
            files={"audio": (filename, audio_bytes, content_type or "audio/webm")},
            headers=headers,
        )
    if resp.status_code != 200:
        logger.error("GPU Worker transcribe failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(resp.status_code, f"GPU Worker error: {resp.text}")

    return resp.json()


@router.post("/transcribe")
async def transcribe_audio(
    request: Request,
    audio: UploadFile = File(..., description="Audio file (webm, wav, mp3, etc.)"),
):
    """Transcribe an audio file to text.

    Uses local Whisper when available, otherwise proxies to the GPU Worker.
    Accepts any audio format supported by ffmpeg (webm, wav, mp3, ogg, etc.).
    """
    if not audio.filename:
        raise HTTPException(400, "No audio file provided")

    t0 = time.perf_counter()

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(400, "Empty audio file")

    # Determine suffix from filename
    suffix = ".webm"
    if audio.filename and "." in audio.filename:
        suffix = "." + audio.filename.rsplit(".", 1)[-1]
    elif audio.content_type:
        ct_map = {
            "audio/webm": ".webm",
            "audio/wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
        }
        suffix = ct_map.get(audio.content_type, ".webm")

    # Try local Whisper first
    try:
        model, backend = _get_whisper_model()
    except Exception as exc:
        logger.info("Local Whisper unavailable (%s)", exc)
        # If this process IS the GPU Worker, don't recurse — return a clean error.
        if os.environ.get("NLS_IS_GPU_WORKER"):
            raise HTTPException(
                503,
                "Whisper not available on this GPU Worker host. "
                "Run: pip install faster-whisper",
            )
        logger.info("Proxying transcription to GPU Worker")
        result = await _proxy_to_gpu_worker(
            audio_bytes, audio.filename or f"audio{suffix}",
            audio.content_type or "audio/webm",
            request=request,
        )
        elapsed = time.perf_counter() - t0
        result["processing_time"] = round(elapsed, 2)
        result["backend"] = "gpu-worker-proxy"
        return result

    # Local Whisper path.
    # Use delete=False + manual cleanup so that on Windows the file is
    # closed (and its exclusive lock released) before faster-whisper / ffmpeg
    # tries to open it by path.  Without this, NamedTemporaryFile holds an
    # exclusive lock on Windows and transcribe() raises [Errno 13] Permission denied.
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        # File is now closed and unlocked — safe for ffmpeg to read on Windows.

        try:
            if backend == "openai-whisper":
                result = model.transcribe(tmp_path, language=None)
                full_text = result["text"].strip()
                language = result.get("language", "en")
                duration = 0.0
                segs = result.get("segments", [])
                if segs:
                    duration = segs[-1].get("end", 0.0)
            else:
                segments, info = model.transcribe(
                    tmp_path, beam_size=5, language=None, vad_filter=True,
                )
                text_parts = [seg.text.strip() for seg in segments]
                full_text = " ".join(text_parts).strip()
                language = info.language
                duration = info.duration

        except Exception as exc:
            logger.error("Transcription failed: %s", exc, exc_info=True)
            raise HTTPException(500, f"Transcription failed: {exc}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    elapsed = time.perf_counter() - t0
    logger.info(
        "Transcribed %.1fs audio -> %d chars in %.1fs (lang=%s, backend=%s)",
        duration, len(full_text), elapsed, language, backend,
    )

    return {
        "text": full_text,
        "language": language,
        "duration": round(duration, 2),
        "processing_time": round(elapsed, 2),
    }
