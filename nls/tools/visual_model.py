"""NLS Visual Model -- Tiered VLM backend for the Visual Cortex.

Hardware-adaptive vision-language model that auto-selects the best
backend for the current machine:

    Apple Silicon  -> FastVLM 0.5B  via mlx-vlm   (~250 ms TTFT)
    CUDA >= 6 GB   -> Moondream 2B  via transformers (~1-3 s)
    CUDA < 6 GB    -> SmolVLM 256M  via transformers (~1-2 s)
    CPU / fallback -> SmolVLM 256M  via transformers (~3-8 s)

A RemoteVLMBackend can call a remote /vision/describe endpoint as a
deep-analysis fallback when configured.

SubprocessVLMBackend wraps any local backend in a dedicated child
process so the main server never loads PyTorch/MPS/CUDA, providing
crash isolation and macOS fork-safety.

SharedVLMRegistry + VLMRequestQueue ensure all agents share one
subprocess and serialize describe calls through a bounded queue that
drops stale frames under load.

All local inference keeps pixels on-device.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_DESCRIBE_KINDS = frozenset({"describe", "describe_fast"})
_STALE_DESCRIBE_RESULT: tuple[str, str] = ("", "")
_STALE_FAST_RESULT = ""

# ---------------------------------------------------------------------------
# Shared prompts
# ---------------------------------------------------------------------------

_DESC_PROMPT = (
    "In one concise sentence, describe the active application, "
    "its main content, and any dialogs or errors visible on screen."
)
_OCR_PROMPT = (
    "List the key visible text: headings, button labels, input values, "
    "error messages, URLs. Skip decorative or repeated text. Be brief."
)

# Richer prompts used when the remote VLM backend is available.
_DESC_PROMPT_RICH = (
    "Describe what is on screen in 2-3 sentences. Include: the application name, "
    "what content is displayed (form fields, error messages, dialogs, page content), "
    "and any notable state (loading, errors, empty fields, validation messages). "
    "Be specific about interactive elements you can see."
)
_OCR_PROMPT_RICH = (
    "Extract key text from the screen: form field labels and values, button text, "
    "error or success messages, page headings, URLs, and any status indicators. "
    "Format each item on its own line. Omit purely decorative text."
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Runtime info about the loaded model."""

    model_id: str
    device: str
    dtype: str
    load_time_s: float
    memory_mb: float = 0.0


@runtime_checkable
class VLMBackend(Protocol):
    """Protocol that every visual-model backend must satisfy."""

    def describe(self, image: Any) -> tuple[str, str]:
        """Return (description, ocr_text) for a PIL Image."""
        ...

    def describe_fast(self, image: Any) -> str:
        """Return a short caption (no OCR). Used by the agent channel."""
        ...

    def warmup(self) -> None:
        """Run dummy inferences to JIT-compile kernels."""
        ...

    def unload(self) -> None:
        """Release model weights and free memory."""
        ...

    @property
    def is_loaded(self) -> bool: ...

    @property
    def is_loading(self) -> bool: ...

    @property
    def info(self) -> ModelInfo | None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _select_device() -> str:
    """Pick the best available torch device."""
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _cuda_vram_gb() -> float:
    """Return total VRAM in GB for the default CUDA device, or 0."""
    try:
        import torch

        if not torch.cuda.is_available():
            return 0.0

        props = torch.cuda.get_device_properties(0)
        vram = props.total_mem / (1024 ** 3)
        if vram > 0:
            logger.info(
                "CUDA device: %s, VRAM: %.1f GB",
                props.name, vram,
            )
            return vram

        # Fallback: torch.cuda.mem_get_info returns (free, total)
        _free, _total = torch.cuda.mem_get_info(0)
        vram = _total / (1024 ** 3)
        if vram > 0:
            logger.info(
                "CUDA device: %s, VRAM (via mem_get_info): %.1f GB",
                props.name, vram,
            )
            return vram
    except Exception as exc:
        logger.warning("CUDA VRAM detection failed: %s", exc)
    return 0.0


# ===================================================================
# Backend: Moondream 2B  (CUDA >= 6 GB)
# ===================================================================

_MOONDREAM_ID = "vikhyatk/moondream2"
_MOONDREAM_REV = "2025-01-09"


class MoondreamBackend:
    """Moondream 2 (2 B params) via transformers.  Best on CUDA."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._device: str = ""
        self._loaded = False
        self._loading = False
        self._info: ModelInfo | None = None

    # -- lifecycle --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loading = True
            t0 = time.perf_counter()
            try:
                from transformers import AutoModelForCausalLM

                self._device = _select_device()
                logger.info("Loading %s (rev %s) on %s...", _MOONDREAM_ID, _MOONDREAM_REV, self._device)
                self._model = AutoModelForCausalLM.from_pretrained(
                    _MOONDREAM_ID,
                    revision=_MOONDREAM_REV,
                    trust_remote_code=True,
                    device_map={"": self._device},
                )
                elapsed = time.perf_counter() - t0
                self._loaded = True
                self._info = ModelInfo(
                    model_id=_MOONDREAM_ID, device=self._device,
                    dtype="auto", load_time_s=round(elapsed, 1),
                )
                logger.info("Moondream loaded in %.1fs on %s", elapsed, self._device)
            except Exception:
                logger.error("Failed to load Moondream", exc_info=True)
                raise
            finally:
                self._loading = False

    def warmup(self) -> None:
        self._ensure_loaded()
        from PIL import Image

        dummy = Image.new("RGB", (768, 576), color=(128, 128, 128))
        try:
            t0 = time.perf_counter()
            self._model.caption(dummy, length="short")
            logger.info("Moondream warmup (caption) done in %.1fs", time.perf_counter() - t0)
        except Exception:
            logger.warning("Moondream warmup failed (non-fatal)", exc_info=True)

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                try:
                    import torch

                    if self._device == "cuda":
                        torch.cuda.empty_cache()
                    elif self._device == "mps":
                        torch.mps.empty_cache()
                except Exception:
                    pass
                logger.info("Moondream unloaded")
            self._loaded = False
            self._info = None

    # -- inference --------------------------------------------------------

    def describe(self, image: Any) -> tuple[str, str]:
        self._ensure_loaded()
        enc = self._model.encode_image(image)
        description = self._model.query(enc, _DESC_PROMPT)["answer"]
        ocr_text = self._model.query(enc, _OCR_PROMPT)["answer"]
        return description, ocr_text

    def describe_fast(self, image: Any) -> str:
        self._ensure_loaded()
        return self._model.caption(image, length="short")["caption"]

    # -- status -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def info(self) -> ModelInfo | None:
        return self._info


# ===================================================================
# Backend: SmolVLM 256M  (universal fallback)
# ===================================================================

_SMOLVLM_ID = "HuggingFaceTB/SmolVLM-256M-Instruct"


class SmolVLMBackend:
    """SmolVLM 256 M params via transformers.  Works everywhere."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None
        self._device: str = ""
        self._loaded = False
        self._loading = False
        self._info: ModelInfo | None = None

    # -- lifecycle --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loading = True
            t0 = time.perf_counter()
            try:
                import torch
                from transformers import AutoModelForVision2Seq, AutoProcessor

                self._device = _select_device()
                dtype = torch.bfloat16 if self._device != "cpu" else torch.float32
                logger.info("Loading %s on %s...", _SMOLVLM_ID, self._device)

                self._processor = AutoProcessor.from_pretrained(_SMOLVLM_ID)
                self._model = AutoModelForVision2Seq.from_pretrained(
                    _SMOLVLM_ID,
                    torch_dtype=dtype,
                    _attn_implementation="eager",
                ).to(self._device)

                elapsed = time.perf_counter() - t0
                self._loaded = True
                self._info = ModelInfo(
                    model_id=_SMOLVLM_ID, device=self._device,
                    dtype=str(dtype), load_time_s=round(elapsed, 1),
                )
                logger.info("SmolVLM loaded in %.1fs on %s", elapsed, self._device)
            except Exception:
                logger.error("Failed to load SmolVLM", exc_info=True)
                raise
            finally:
                self._loading = False

    def warmup(self) -> None:
        self._ensure_loaded()
        from PIL import Image

        dummy = Image.new("RGB", (384, 384), color=(128, 128, 128))
        try:
            t0 = time.perf_counter()
            self._generate(dummy, "Describe this image briefly.")
            logger.info("SmolVLM warmup done in %.1fs", time.perf_counter() - t0)
        except Exception:
            logger.warning("SmolVLM warmup failed (non-fatal)", exc_info=True)

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                del self._processor
                self._processor = None
                try:
                    import torch

                    if self._device == "cuda":
                        torch.cuda.empty_cache()
                    elif self._device == "mps":
                        torch.mps.empty_cache()
                except Exception:
                    pass
                logger.info("SmolVLM unloaded")
            self._loaded = False
            self._info = None

    # -- inference --------------------------------------------------------

    def _generate(self, image: Any, prompt: str, max_tokens: int = 300) -> str:
        """Run a single VLM generation pass."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        text = self._processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self._processor(text=text, images=[image], return_tensors="pt").to(self._device)
        ids = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        # Decode only the generated tokens (skip the prompt)
        generated = ids[0, inputs["input_ids"].shape[1]:]
        return self._processor.decode(generated, skip_special_tokens=True).strip()

    def describe(self, image: Any) -> tuple[str, str]:
        self._ensure_loaded()
        description = self._generate(image, _DESC_PROMPT, max_tokens=300)
        ocr_text = self._generate(image, _OCR_PROMPT, max_tokens=500)
        return description, ocr_text

    def describe_fast(self, image: Any) -> str:
        self._ensure_loaded()
        return self._generate(image, "Describe this screen briefly.", max_tokens=150)

    # -- status -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def info(self) -> ModelInfo | None:
        return self._info


# ===================================================================
# Backend: FastVLM 0.5B  (Apple Silicon via mlx-vlm)
# ===================================================================

_FASTVLM_ID = "mlx-community/FastVLM-0.5B-bf16"


class FastVLMBackend:
    """Apple FastVLM 0.5 B via mlx-vlm.  Apple Silicon only."""

    def __init__(self) -> None:
        self._model: Any = None
        self._processor: Any = None
        self._config: Any = None
        self._loaded = False
        self._loading = False
        self._info: ModelInfo | None = None

    # -- lifecycle --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loading = True
        t0 = time.perf_counter()
        try:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            logger.info("Loading %s via mlx-vlm...", _FASTVLM_ID)
            self._model, self._processor = load(_FASTVLM_ID)
            self._config = load_config(_FASTVLM_ID)
            elapsed = time.perf_counter() - t0
            self._loaded = True
            self._info = ModelInfo(
                model_id=_FASTVLM_ID, device="mlx",
                dtype="bf16", load_time_s=round(elapsed, 1),
            )
            logger.info("FastVLM loaded in %.1fs via MLX", elapsed)
        except Exception:
            logger.error("Failed to load FastVLM", exc_info=True)
            raise
        finally:
            self._loading = False

    def warmup(self) -> None:
        self._ensure_loaded()
        from PIL import Image

        dummy = Image.new("RGB", (384, 384), color=(128, 128, 128))
        try:
            t0 = time.perf_counter()
            self._generate(dummy, "Describe this image briefly.")
            logger.info("FastVLM warmup done in %.1fs", time.perf_counter() - t0)
        except Exception:
            logger.warning("FastVLM warmup failed (non-fatal)", exc_info=True)

    def unload(self) -> None:
        self._model = None
        self._processor = None
        self._config = None
        self._loaded = False
        self._info = None
        logger.info("FastVLM unloaded")

    # -- inference --------------------------------------------------------

    def _generate(self, image: Any, prompt: str, max_tokens: int = 300) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        formatted = apply_chat_template(self._processor, self._config, prompt, num_images=1)
        # mlx_vlm.generate expects a list of image paths or PIL images
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(buf.getvalue())
            tmp_path = tmp.name
        try:
            return generate(
                self._model, self._processor, formatted, [tmp_path],
                max_tokens=max_tokens, temperature=0.0, verbose=False,
            )
        finally:
            os.unlink(tmp_path)

    def describe(self, image: Any) -> tuple[str, str]:
        self._ensure_loaded()
        description = self._generate(image, _DESC_PROMPT, max_tokens=300)
        ocr_text = self._generate(image, _OCR_PROMPT, max_tokens=500)
        return description, ocr_text

    def describe_fast(self, image: Any) -> str:
        self._ensure_loaded()
        return self._generate(image, "Describe this screen briefly.", max_tokens=150)

    # -- status -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def info(self) -> ModelInfo | None:
        return self._info


# ===================================================================
# Backend: Remote VLM
# ===================================================================


class RemoteVLMBackend:
    """Calls a remote /vision/describe endpoint.

    Requires ``gpu_worker_url`` and ``gpu_worker_secret`` to be set.
    Does NOT load any model locally.
    """

    def __init__(self, gpu_worker_url: str, gpu_worker_secret: str = "") -> None:
        self._url = gpu_worker_url.rstrip("/")
        self._secret = gpu_worker_secret
        self._info = ModelInfo(
            model_id="remote:qwen2.5-vl-3b",
            device="remote",
            dtype="remote",
            load_time_s=0.0,
        )

    def _image_to_b64(self, image: Any) -> str:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode()

    def _post(self, endpoint: str, data: dict[str, str]) -> dict[str, Any]:
        import httpx
        import os

        headers: dict[str, str] = {}
        if self._secret:
            headers["X-GPU-Worker-Secret"] = self._secret
        # Nest GPU relay (`/api/gpu/*`) uses CloudAuthGuard — same bearer as inference.
        bearer = os.environ.get("NLS_INFERENCE_API_KEY", "").strip()
        if bearer and (
            "api.babo.agency" in self._url
            or "/api/gpu" in self._url
        ):
            headers["Authorization"] = f"Bearer {bearer}"
        resp = httpx.post(
            f"{self._url}{endpoint}",
            data=data,
            headers=headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    # -- lifecycle (no-op for remote) -------------------------------------

    def warmup(self) -> None:
        pass

    def unload(self) -> None:
        pass

    # -- inference --------------------------------------------------------

    def describe(self, image: Any) -> tuple[str, str]:
        b64 = self._image_to_b64(image)
        result = self._post("/vision/describe", {"image_base64": b64})
        return result.get("description", ""), result.get("ocr_text", "")

    def describe_fast(self, image: Any) -> str:
        b64 = self._image_to_b64(image)
        result = self._post("/vision/describe", {"image_base64": b64})
        return result.get("description", "")

    def ask(self, image: Any, question: str) -> str:
        """Ask a targeted question about the image."""
        b64 = self._image_to_b64(image)
        result = self._post("/vision/ask", {"image_base64": b64, "question": question})
        return result.get("answer", "")

    # -- status -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return True

    @property
    def is_loading(self) -> bool:
        return False

    @property
    def info(self) -> ModelInfo | None:
        return self._info


# ===================================================================
# Backend: Subprocess isolation  (crash + fork safety)
# ===================================================================


class SubprocessVLMBackend:
    """Proxy that runs any local VLM in a dedicated child process.

    Benefits (all platforms):
      * **Crash isolation** — VLM OOM / segfault does not take down the
        server; the subprocess is automatically restarted.
      * **Fork safety (macOS)** — PyTorch MPS initialises Metal/ObjC;
        any later ``fork()`` creates zombie processes at 100 % CPU.
        Keeping PyTorch out of the main process avoids this entirely.

    Spawns ``vlm_worker.py`` via ``Popen`` and proxies the
    ``VLMBackend`` protocol over JSON-over-pipes.  The main server
    process never imports ``torch``.
    """

    _MAX_RESTARTS = 3

    def __init__(self, preference: str = "auto") -> None:
        self._preference = preference
        self._proc: subprocess.Popen | None = None
        self._loaded = False
        self._loading = False
        self._info: ModelInfo | None = None
        self._lock = threading.Lock()
        self._restarts = 0

    # -- IPC helpers -------------------------------------------------------

    def _spawn(self) -> None:
        """Start (or restart) the worker subprocess."""
        if self._proc is not None and self._proc.poll() is None:
            return
        worker = os.path.join(os.path.dirname(__file__), "vlm_worker.py")
        # The worker does `from nls.engine.visual_model import select_backend`.
        # Ensure the package root is on PYTHONPATH so the subprocess can
        # resolve the `nls` package even when launched from a bundled app.
        pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__),
        )))
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            pkg_root + os.pathsep + existing if existing else pkg_root
        )
        self._proc = subprocess.Popen(
            [sys.executable, worker],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # inherit parent stderr → shows in runtime log
            text=True,
            bufsize=1,
            env=env,
        )
        logger.info(
            "VLM subprocess spawned (pid=%d, preference=%s)",
            self._proc.pid, self._preference,
        )

    def _send(self, req: dict, timeout: float = 60.0) -> dict:
        """Send a JSON request and read the JSON response.

        Uses a background thread for the blocking readline so we get
        cross-platform timeout support (``select`` doesn't work on
        Windows pipes).
        """
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("VLM subprocess is not running")
        line = json.dumps(req) + "\n"
        try:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"VLM subprocess stdin write failed: {exc}") from exc

        result_box: list[str | None] = [None]

        def _reader() -> None:
            try:
                assert self._proc is not None and self._proc.stdout is not None
                result_box[0] = self._proc.stdout.readline()
            except Exception:
                result_box[0] = None

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            logger.warning("VLM subprocess timed out after %.0fs — killing", timeout)
            self._kill()
            raise TimeoutError(f"VLM subprocess did not respond in {timeout}s")

        resp_line = result_box[0]
        if not resp_line:
            raise RuntimeError("VLM subprocess closed stdout (crashed?)")
        return json.loads(resp_line)

    def _kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
        self._proc = None
        self._loaded = False

    def _ensure_alive(self) -> None:
        """Re-spawn + re-warmup if the subprocess died."""
        if self._proc is not None and self._proc.poll() is None:
            return
        if self._restarts >= self._MAX_RESTARTS:
            raise RuntimeError(
                f"VLM subprocess crashed {self._restarts} times — giving up",
            )
        logger.warning(
            "VLM subprocess died (restarts=%d/%d) — restarting",
            self._restarts, self._MAX_RESTARTS,
        )
        self._restarts += 1
        self._loaded = False
        self._spawn()
        resp = self._send(
            {"cmd": "warmup", "preference": self._preference},
            timeout=120.0,
        )
        if resp.get("ok"):
            self._loaded = True
            info_d = resp.get("info", {})
            if info_d:
                self._info = ModelInfo(**info_d)

    # -- VLMBackend protocol -----------------------------------------------

    def warmup(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loading = True
            try:
                self._spawn()
                resp = self._send(
                    {"cmd": "warmup", "preference": self._preference},
                    timeout=120.0,
                )
                if not resp.get("ok"):
                    raise RuntimeError(
                        f"VLM worker warmup failed: {resp.get('error', 'unknown')}",
                    )
                self._loaded = True
                self._restarts = 0
                info_d = resp.get("info", {})
                if info_d:
                    self._info = ModelInfo(**info_d)
                logger.info(
                    "SubprocessVLMBackend ready (pid=%d, model=%s)",
                    self._proc.pid if self._proc else -1,
                    self._info.model_id if self._info else "?",
                )
            finally:
                self._loading = False

    def describe(self, image: Any) -> tuple[str, str]:
        with self._lock:
            self._ensure_alive()
            b64 = self._image_to_b64(image)
            try:
                resp = self._send(
                    {"cmd": "describe", "image_b64": b64},
                    timeout=45.0,
                )
            except (RuntimeError, TimeoutError):
                self._ensure_alive()
                resp = self._send(
                    {"cmd": "describe", "image_b64": b64},
                    timeout=45.0,
                )
            if not resp.get("ok"):
                raise RuntimeError(
                    f"VLM describe failed: {resp.get('error', 'unknown')}",
                )
            return resp.get("desc", ""), resp.get("ocr", "")

    def describe_fast(self, image: Any) -> str:
        with self._lock:
            self._ensure_alive()
            b64 = self._image_to_b64(image)
            try:
                resp = self._send(
                    {"cmd": "describe_fast", "image_b64": b64},
                    timeout=45.0,
                )
            except (RuntimeError, TimeoutError):
                self._ensure_alive()
                resp = self._send(
                    {"cmd": "describe_fast", "image_b64": b64},
                    timeout=45.0,
                )
            if not resp.get("ok"):
                raise RuntimeError(
                    f"VLM describe_fast failed: {resp.get('error', 'unknown')}",
                )
            return resp.get("desc", "")

    def unload(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._send({"cmd": "shutdown"}, timeout=10.0)
                except Exception:
                    pass
            self._kill()
            self._info = None
            self._loaded = False

    # -- status ------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def info(self) -> ModelInfo | None:
        return self._info

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _image_to_b64(image: Any) -> str:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode()


# ===================================================================
# Process-wide VLM request queue (one worker thread, bounded backlog)
# ===================================================================


@dataclass
class _VLMJob:
    kind: str
    fn: Callable[[], Any]
    future: Future[Any]
    enqueued_at: float


class VLMRequestQueue:
    """Serialize VLM work from all agents through one dispatcher thread.

    Describe requests are bounded (``NLS_VLM_QUEUE_MAX``, default 8).
    When the backlog is full, the oldest pending describe jobs are dropped
    with an empty result so callers do not pile up 45s timeouts.
    Warmup/unload jobs are prioritized to the front of the queue.
    """

    def __init__(self, *, max_pending: int | None = None) -> None:
        raw = max_pending if max_pending is not None else int(
            os.environ.get("NLS_VLM_QUEUE_MAX", "8"),
        )
        self._max_pending = max(1, raw)
        self._pending: deque[_VLMJob] = deque()
        self._cond = threading.Condition()
        self._shutdown = False
        self._worker = threading.Thread(
            target=self._loop,
            name="vlm-request-queue",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        kind: str,
        fn: Callable[[], _T],
        *,
        timeout: float = 60.0,
        priority: bool = False,
    ) -> _T:
        if self._shutdown and kind != "shutdown":
            raise RuntimeError("VLM request queue is shut down")

        future: Future[Any] = Future()
        job = _VLMJob(kind=kind, fn=fn, future=future, enqueued_at=time.time())

        with self._cond:
            if self._shutdown and kind != "shutdown":
                raise RuntimeError("VLM request queue is shut down")
            if kind in _DESCRIBE_KINDS:
                self._trim_describe_backlog_locked()
            if priority:
                self._pending.appendleft(job)
            else:
                self._pending.append(job)
            self._cond.notify()

        return future.result(timeout=timeout)

    def initiate_shutdown(self, *, drain: bool = False) -> None:
        """Stop accepting describe jobs; optionally keep pending work."""
        with self._cond:
            if self._shutdown:
                return
            self._shutdown = True
            if not drain:
                self._cancel_pending_describes_locked()

    def finalize_shutdown(self) -> None:
        """Drain the worker thread after unload (or other teardown work)."""
        with self._cond:
            self._pending.append(
                _VLMJob(
                    kind="shutdown",
                    fn=lambda: None,
                    future=Future(),
                    enqueued_at=time.time(),
                ),
            )
            self._cond.notify_all()
        self._worker.join(timeout=5.0)

    def shutdown(self, *, drain: bool = False) -> None:
        self.initiate_shutdown(drain=drain)
        self.finalize_shutdown()

    def stats(self) -> dict[str, int]:
        with self._cond:
            pending = len(self._pending)
            describe_pending = sum(
                1 for j in self._pending if j.kind in _DESCRIBE_KINDS
            )
        return {
            "pending": pending,
            "describe_pending": describe_pending,
            "max_pending": self._max_pending,
        }

    def _trim_describe_backlog_locked(self) -> None:
        describe_jobs = [j for j in self._pending if j.kind in _DESCRIBE_KINDS]
        overflow = len(describe_jobs) - self._max_pending + 1
        if overflow <= 0:
            return
        dropped = 0
        for _ in range(overflow):
            for idx, job in enumerate(self._pending):
                if job.kind not in _DESCRIBE_KINDS:
                    continue
                del self._pending[idx]
                self._resolve_dropped(job)
                dropped += 1
                break
        if dropped:
            logger.info(
                "VLM queue: dropped %d stale describe job(s) (max_pending=%d)",
                dropped,
                self._max_pending,
            )

    def _cancel_pending_describes_locked(self) -> None:
        kept: deque[_VLMJob] = deque()
        dropped = 0
        for job in self._pending:
            if job.kind in _DESCRIBE_KINDS:
                self._resolve_dropped(job)
                dropped += 1
            else:
                kept.append(job)
        self._pending = kept
        if dropped:
            logger.info(
                "VLM queue: cancelled %d pending describe job(s) on shutdown",
                dropped,
            )

    @staticmethod
    def _resolve_dropped(job: _VLMJob) -> None:
        if job.future.done():
            return
        if job.kind == "describe_fast":
            job.future.set_result(_STALE_FAST_RESULT)
        elif job.kind == "describe":
            job.future.set_result(_STALE_DESCRIBE_RESULT)
        else:
            job.future.cancel()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait()
                job = self._pending.popleft()

            if job.kind == "shutdown":
                break

            if job.future.cancelled():
                continue

            try:
                job.future.set_result(job.fn())
            except Exception as exc:
                if not job.future.done():
                    job.future.set_exception(exc)

        with self._cond:
            self._cancel_pending_describes_locked()


class QueuedVLMBackend:
    """VLMBackend-shaped proxy: all work goes through ``VLMRequestQueue``."""

    def __init__(
        self,
        backend: SubprocessVLMBackend,
        request_queue: VLMRequestQueue,
    ) -> None:
        self._backend = backend
        self._queue = request_queue

    def warmup(self) -> None:
        if self._backend.is_loaded or self._backend.is_loading:
            return
        self._queue.submit(
            "warmup",
            self._backend.warmup,
            timeout=120.0,
            priority=True,
        )

    def describe(self, image: Any) -> tuple[str, str]:
        return self._queue.submit(
            "describe",
            lambda: self._backend.describe(image),
            timeout=50.0,
        )

    def describe_fast(self, image: Any) -> str:
        return self._queue.submit(
            "describe_fast",
            lambda: self._backend.describe_fast(image),
            timeout=50.0,
        )

    def unload(self) -> None:
        self._queue.submit(
            "unload",
            self._backend.unload,
            timeout=15.0,
            priority=True,
        )

    @property
    def is_loaded(self) -> bool:
        return self._backend.is_loaded

    @property
    def is_loading(self) -> bool:
        return self._backend.is_loading

    @property
    def info(self) -> ModelInfo | None:
        return self._backend.info


# ===================================================================
# Process-wide shared VLM worker (one subprocess for all agents)
# ===================================================================


class SharedVLMRegistry:
    """Reference-counted pool of shared VLM workers + request queues.

    Each agent gets its own ``VisualCortex`` capture loop, but all agents
    share one local VLM subprocess **and** one ``VLMRequestQueue`` per
    model preference.  Without this, loading N agents spawns N SmolVLM
    workers on CUDA and floods the GPU with parallel describe calls.
    """

    _lock = threading.Lock()
    _backends: dict[str, SubprocessVLMBackend] = {}
    _queues: dict[str, VLMRequestQueue] = {}
    _refcounts: dict[str, int] = {}

    @classmethod
    def acquire(cls, preference: str = "auto") -> QueuedVLMBackend:
        key = preference or "auto"
        with cls._lock:
            backend = cls._backends.get(key)
            queue = cls._queues.get(key)
            if backend is None or queue is None:
                backend = SubprocessVLMBackend(preference=key)
                queue = VLMRequestQueue()
                cls._backends[key] = backend
                cls._queues[key] = queue
                logger.info(
                    "SharedVLM: created worker+queue for preference=%r",
                    key,
                )
            cls._refcounts[key] = cls._refcounts.get(key, 0) + 1
            logger.info(
                "SharedVLM: acquire preference=%r refs=%d queue=%s pid=%s",
                key,
                cls._refcounts[key],
                queue.stats(),
                backend._proc.pid if getattr(backend, "_proc", None) else "pending",
            )
            return QueuedVLMBackend(backend, queue)

    @classmethod
    def release(cls, preference: str = "auto") -> None:
        key = preference or "auto"
        with cls._lock:
            rc = cls._refcounts.get(key, 0)
            if rc <= 0:
                logger.warning(
                    "SharedVLM: release preference=%r with refs=0 (ignored)",
                    key,
                )
                return
            rc -= 1
            cls._refcounts[key] = rc
            backend = cls._backends.get(key)
            queue = cls._queues.get(key)
            logger.info(
                "SharedVLM: release preference=%r refs=%d queue=%s",
                key,
                rc,
                queue.stats() if queue else {},
            )
            if rc == 0:
                if queue is not None:
                    # Reject new describes before unload so a stopping agent's
                    # in-flight executor thread cannot respawn the worker.
                    queue.initiate_shutdown()
                    if backend is not None and backend.is_loaded:
                        try:
                            queue.submit(
                                "unload",
                                backend.unload,
                                timeout=15.0,
                                priority=True,
                            )
                        except Exception as exc:
                            logger.warning(
                                "SharedVLM: queued unload failed: %s", exc,
                            )
                            if backend is not None:
                                backend.unload()
                    queue.finalize_shutdown()
                elif backend is not None:
                    backend.unload()
                cls._backends.pop(key, None)
                cls._queues.pop(key, None)
                cls._refcounts.pop(key, None)
                logger.info("SharedVLM: worker unloaded preference=%r", key)


# ===================================================================
# Backend selection
# ===================================================================


def select_backend(preference: str = "auto") -> VLMBackend:
    """Pick the best local VLM backend for the current hardware.

    Args:
        preference: "auto", "moondream", "smolvlm", "fastvlm"
    """
    if preference == "moondream":
        logger.info("VLM backend: MoondreamBackend (forced by preference)")
        return MoondreamBackend()

    if preference == "smolvlm":
        logger.info("VLM backend: SmolVLMBackend (forced by preference)")
        return SmolVLMBackend()

    if preference == "fastvlm":
        logger.info("VLM backend: FastVLMBackend (forced by preference)")
        return FastVLMBackend()

    # Auto-detect
    device = "cpu"
    try:
        import torch

        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        pass

    # Apple Silicon -> try FastVLM via MLX, fall back to SmolVLM
    if device == "mps":
        try:
            import mlx_vlm  # noqa: F401

            logger.info("VLM backend: FastVLMBackend (Apple Silicon + mlx-vlm available)")
            return FastVLMBackend()
        except ImportError:
            logger.info("VLM backend: SmolVLMBackend (Apple Silicon, mlx-vlm not installed)")
            return SmolVLMBackend()

    # CUDA with enough VRAM -> Moondream 2B
    if device == "cuda":
        vram = _cuda_vram_gb()
        if vram >= 6.0:
            logger.info("VLM backend: MoondreamBackend (CUDA %.1f GB VRAM)", vram)
            return MoondreamBackend()
        else:
            logger.info("VLM backend: SmolVLMBackend (CUDA %.1f GB VRAM — too low for Moondream)", vram)
            return SmolVLMBackend()

    # CPU fallback
    logger.info("VLM backend: SmolVLMBackend (CPU fallback)")
    return SmolVLMBackend()


# Keep for backward compat — callers that used MoondreamLocal
MoondreamLocal = MoondreamBackend
