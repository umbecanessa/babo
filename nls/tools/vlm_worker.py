"""VLM Worker -- Isolated subprocess for vision-language model inference.

Runs as a standalone subprocess spawned by SubprocessVLMBackend.
Communicates via JSON-over-pipes (stdin/stdout, one JSON object per line).
Loads PyTorch/MPS/MLX/CUDA *only* in this process so the parent server
stays lightweight and crash-isolated on every platform.

Protocol
--------
Request  (parent → worker, one JSON per line on stdin):
    {"cmd": "warmup", "preference": "auto"}
    {"cmd": "describe", "image_b64": "<base64 JPEG>"}
    {"cmd": "describe_fast", "image_b64": "<base64 JPEG>"}
    {"cmd": "unload"}
    {"cmd": "status"}
    {"cmd": "shutdown"}

Response (worker → parent, one JSON per line on stdout):
    {"ok": true, ...}
    {"ok": false, "error": "..."}

Stderr is kept for logging/diagnostics and forwarded by the parent.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
import traceback
from dataclasses import asdict
from typing import Any

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | vlm_worker | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vlm_worker")

_backend: Any = None


def _respond(obj: dict) -> None:
    """Write a single JSON response line to stdout."""
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _image_from_b64(b64: str) -> Any:
    """Decode a base64 JPEG string into a PIL RGB Image."""
    from PIL import Image

    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _handle_warmup(preference: str) -> dict:
    global _backend
    from nls.tools.visual_model import select_backend

    logger.info("Warming up with preference=%s", preference)
    _backend = select_backend(preference)
    _backend.warmup()
    info = asdict(_backend.info) if _backend.info else {}
    logger.info("Warmup complete: %s", info.get("model_id", "unknown"))
    return {"ok": True, "info": info}


def _handle_describe(image_b64: str) -> dict:
    if _backend is None:
        return {"ok": False, "error": "VLM not loaded — call warmup first"}
    image = _image_from_b64(image_b64)
    desc, ocr = _backend.describe(image)
    return {"ok": True, "desc": desc, "ocr": ocr}


def _handle_describe_fast(image_b64: str) -> dict:
    if _backend is None:
        return {"ok": False, "error": "VLM not loaded — call warmup first"}
    image = _image_from_b64(image_b64)
    desc = _backend.describe_fast(image)
    return {"ok": True, "desc": desc}


def _handle_unload() -> dict:
    global _backend
    if _backend is not None:
        _backend.unload()
        _backend = None
    return {"ok": True}


def _handle_status() -> dict:
    if _backend is None:
        return {"ok": True, "loaded": False, "loading": False, "info": None}
    return {
        "ok": True,
        "loaded": _backend.is_loaded,
        "loading": _backend.is_loading,
        "info": asdict(_backend.info) if _backend.info else None,
    }


def main() -> None:
    logger.info("VLM worker started (pid=%d)", __import__("os").getpid())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _respond({"ok": False, "error": f"Invalid JSON: {exc}"})
            continue

        cmd = req.get("cmd", "")
        try:
            if cmd == "warmup":
                _respond(_handle_warmup(req.get("preference", "auto")))
            elif cmd == "describe":
                _respond(_handle_describe(req["image_b64"]))
            elif cmd == "describe_fast":
                _respond(_handle_describe_fast(req["image_b64"]))
            elif cmd == "unload":
                _respond(_handle_unload())
            elif cmd == "status":
                _respond(_handle_status())
            elif cmd == "shutdown":
                _respond({"ok": True})
                logger.info("Shutdown requested — exiting")
                break
            else:
                _respond({"ok": False, "error": f"Unknown command: {cmd}"})
        except Exception:
            tb = traceback.format_exc()
            logger.error("Command %s failed:\n%s", cmd, tb)
            _respond({"ok": False, "error": tb[-500:]})

    _handle_unload()
    logger.info("VLM worker exiting")


if __name__ == "__main__":
    main()
