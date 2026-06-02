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
        self._remote_inference = self._is_remote_vllm(vllm_base_url)

    @staticmethod
    def _is_remote_vllm(base_url: str) -> bool:
        from urllib.parse import urlparse

        try:
            host = (urlparse(base_url).hostname or "").lower()
        except Exception:
            return True
        return host not in ("localhost", "127.0.0.1", "::1")

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

    _GENERIC_TOKENIZER_REPOS: tuple[str, ...] = (
        "Qwen/Qwen2.5-0.5B-Instruct",
        "Qwen/Qwen3-32B",
        "gpt2",
    )

    def _load_tokenizer(self, auto_tokenizer_cls: Any) -> Any:
        import os

        model_id = self.hf_model
        if self._remote_inference or self._is_api_model_id(model_id):
            return self._load_generic_tokenizer(
                auto_tokenizer_cls,
                f"remote inference model {model_id!r}",
            )

        if os.path.isdir(model_id):
            return self._finalize_tokenizer(
                auto_tokenizer_cls.from_pretrained(model_id),
            )

        if "/" in model_id and not model_id.startswith("/"):
            try:
                return self._finalize_tokenizer(
                    auto_tokenizer_cls.from_pretrained(model_id),
                )
            except Exception as exc:
                logger.warning(
                    "Tokenizer for %s unavailable (%s); using generic fallback",
                    model_id,
                    exc,
                )
                return self._load_generic_tokenizer(
                    auto_tokenizer_cls,
                    f"HuggingFace repo {model_id!r}",
                )

        for key, repo in self._TOKENIZER_FALLBACKS.items():
            if key in model_id:
                try:
                    return self._finalize_tokenizer(
                        auto_tokenizer_cls.from_pretrained(repo),
                    )
                except Exception as exc:
                    logger.warning("Fallback tokenizer %s failed: %s", repo, exc)

        try:
            return self._finalize_tokenizer(
                auto_tokenizer_cls.from_pretrained(model_id),
            )
        except Exception as exc:
            logger.warning(
                "Tokenizer for %s failed (%s); using generic fallback",
                model_id,
                exc,
            )
            return self._load_generic_tokenizer(
                auto_tokenizer_cls,
                f"model id {model_id!r}",
            )

    @staticmethod
    def _is_api_model_id(model_id: str) -> bool:
        if model_id in ("babo-hosted",):
            return True
        if "/" not in model_id:
            return model_id.startswith(("gpt-", "claude-", "o1", "o3", "gemini-"))
        provider = model_id.split("/", 1)[0].lower()
        return provider in {
            "openai",
            "anthropic",
            "google",
            "meta-llama",
            "mistralai",
            "deepseek",
            "cohere",
            "x-ai",
            "perplexity",
            "openrouter",
            "qwen",
            "microsoft",
            "nvidia",
            "amazon",
        }

    def _load_generic_tokenizer(
        self,
        auto_tokenizer_cls: Any,
        reason: str,
    ) -> Any:
        last_exc: Exception | None = None
        for repo in self._GENERIC_TOKENIZER_REPOS:
            try:
                tok = self._finalize_tokenizer(
                    auto_tokenizer_cls.from_pretrained(repo),
                )
                logger.info(
                    "Loaded generic tokenizer %s for %s (%s)",
                    repo,
                    self.hf_model,
                    reason,
                )
                return tok
            except Exception as exc:
                last_exc = exc
                logger.warning("Generic tokenizer %s failed: %s", repo, exc)
        raise RuntimeError(
            f"Could not load any tokenizer for inference model {self.hf_model!r}",
        ) from last_exc

    @staticmethod
    def _finalize_tokenizer(tok: Any) -> Any:
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
