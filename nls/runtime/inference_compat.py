"""Upstream-aware inference helpers (local vLLM vs Babo Cloud / OpenRouter)."""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

# OpenRouter: only these families need tool_choice=required (reasoning-only on auto).
_CLOUD_TOOL_REQUIRED_MODELS = (
    "qwen3.6",
    "qwen3-6",
    "qwen/qwen3.6",
)


def resolve_agent_inference(
    runtime: Any,
    model_override: str | None = None,
) -> tuple[Any, str | None]:
    """Resolve (client, adapter) for any inference on an agent runtime.

    Prefer ``runtime.inference_pipeline()`` so cloud session models never
    fall back to the install-default LAN client.
    """
    if runtime is None:
        return None, None
    pipeline = getattr(runtime, "inference_pipeline", None)
    if callable(pipeline):
        return pipeline(model_override)
    client = getattr(runtime, "vllm_client", None)
    adapter = (model_override or "").strip() or None
    return client, adapter


def agent_inference_available(
    runtime: Any,
    model_override: str | None = None,
) -> bool:
    """True when this agent has a routed inference client for the given model."""
    client, _ = resolve_agent_inference(runtime, model_override)
    return client is not None


def inference_host_is_local(base_url: str) -> bool:
    """True when the client points at local or private-LAN inference (not cloud relay)."""
    try:
        host = (urlparse((base_url or "").strip()).hostname or "").lower()
    except Exception:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def resolve_tool_choice(
    base_url: str,
    *,
    has_tools: bool,
    model: str | None = None,
) -> str | None:
    """Pick tool_choice for chat completions.

    Local vLLM uses ``auto``. On cloud, only models known to ignore tools on
    ``auto`` (Qwen 3.6) use ``required``; others use ``auto``.
    """
    if not has_tools:
        return None
    if inference_host_is_local(base_url):
        return "auto"
    m = (model or "").lower()
    if any(token in m for token in _CLOUD_TOOL_REQUIRED_MODELS):
        return "required"
    return "auto"


def micro_inference_extra_body(
    base_url: str,
    *,
    thinking: bool = False,
) -> dict[str, Any]:
    """Request extras for short classifier / extraction micro-calls."""
    return cloud_safe_extra_body(
        base_url,
        {},
        thinking=thinking,
        is_continuation=False,
    )


def cloud_safe_extra_body(
    base_url: str,
    extra_body: dict[str, Any],
    *,
    thinking: bool,
    is_continuation: bool,
) -> dict[str, Any]:
    """Drop vLLM-only request fields when talking to OpenRouter relays."""
    if inference_host_is_local(base_url):
        body = dict(extra_body)
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
        if is_continuation:
            body["continue_final_message"] = True
            body["add_generation_prompt"] = False
        return body

    skip = {
        "chat_template_kwargs",
        "vllm_xargs",
        "continue_final_message",
        "add_generation_prompt",
    }
    return {k: v for k, v in extra_body.items() if k not in skip}
