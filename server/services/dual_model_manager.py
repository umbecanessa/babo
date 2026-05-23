"""Inference client manager — OpenAI-compatible backend + local tokenizer."""

from __future__ import annotations

import gc
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DualModelManager:
    """Manages the inference HTTP client and local tokenizer."""

    def __init__(
        self,
        hf_model: str,
        vllm_base_url: str = "http://localhost:8000",
        attn_implementation: str = "",
        genesis_dir: Path | None = None,
        agents_dir: Path | None = None,
        default_genesis: str = "standard-v1",
        inference_api_key: str = "",
        product_mode: bool = True,
    ):
        self.hf_model = hf_model
        self.vllm_base_url = vllm_base_url
        self.inference_api_key = inference_api_key
        self.product_mode = product_mode
        self.attn_implementation = attn_implementation
        self.genesis_dir = genesis_dir
        self.agents_dir = agents_dir
        self.default_genesis = default_genesis

        self.vllm_client = None
        self.tokenizer = None
        self.model_a = None
        self._loaded = False

    def load_models(self) -> None:
        import transformers
        from transformers import AutoTokenizer

        from server.services.vllm_client import VLLMInferenceClient

        transformers.logging.set_verbosity_error()

        logger.info("Initializing inference client -> %s", self.vllm_base_url)
        t0 = time.perf_counter()

        self.vllm_client = VLLMInferenceClient(
            base_url=self.vllm_base_url,
            default_model=self.hf_model,
            api_key=self.inference_api_key or None,
        )
        self.model_a = self.vllm_client  # type: ignore[assignment]
        self.tokenizer = self._load_tokenizer(AutoTokenizer)

        self._loaded = True
        logger.info(
            "Inference client ready in %.1fs (model=%s)",
            time.perf_counter() - t0,
            self.hf_model,
        )

    _TOKENIZER_FALLBACKS: dict[str, str] = {
        "qwen35-nls": "Qwen/Qwen3.5-35B-A3B",
        "Qwen3.5-35B-A3B": "Qwen/Qwen3.5-35B-A3B",
        "Qwen3-32B": "Qwen/Qwen3-32B",
    }

    def _load_tokenizer(self, auto_tokenizer_cls: Any) -> Any:
        import os

        model_id = self.hf_model
        if os.path.isdir(model_id) or "/" in model_id and not model_id.startswith("/"):
            try:
                tok = auto_tokenizer_cls.from_pretrained(model_id)
                if tok.pad_token is None:
                    tok.pad_token = tok.eos_token
                return tok
            except Exception:
                pass

        for key, repo in self._TOKENIZER_FALLBACKS.items():
            if key in model_id:
                try:
                    tok = auto_tokenizer_cls.from_pretrained(repo)
                    if tok.pad_token is None:
                        tok.pad_token = tok.eos_token
                    return tok
                except Exception as exc:
                    logger.warning("Fallback tokenizer %s failed: %s", repo, exc)

        tok = auto_tokenizer_cls.from_pretrained(model_id)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        return tok

    def unload(self) -> None:
        if self.vllm_client is not None:
            self.vllm_client = None
            self.model_a = None
        if self.tokenizer is not None:
            del self.tokenizer
            self.tokenizer = None

        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except ImportError:
            pass

        self._loaded = False
        logger.info("Inference client closed")

    async def async_unload(self) -> None:
        if self.vllm_client is not None:
            await self.vllm_client.close()
        self.unload()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "loaded": self._loaded,
            "hf_model": self.hf_model,
            "inference_backend": "openai_compatible",
            "vllm_base_url": self.vllm_base_url,
            "vllm_client": (
                self.vllm_client.get_status()
                if self.vllm_client is not None
                else None
            ),
        }
        try:
            import torch
            if torch.cuda.is_available():
                status["vram_used_mb"] = round(
                    torch.cuda.memory_allocated() / 1024 / 1024, 1,
                )
        except Exception:
            pass
        return status
