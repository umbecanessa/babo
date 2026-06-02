"""Upstream-aware inference helpers (local vLLM vs Babo Cloud / OpenRouter)."""
from __future__ import annotations

import copy
import ipaddress
from typing import Any
from urllib.parse import urlparse

# OpenRouter: only these families need tool_choice=required (reasoning-only on auto).
_CLOUD_TOOL_REQUIRED_MODELS = (
    "qwen3.6",
    "qwen3-6",
    "qwen/qwen3.6",
)

# Qwen / hosted brain models that honor /no_think and burn micro-call budgets on reasoning.
_QWEN_MODEL_MARKERS = (
    "qwen",
    "babo-hosted",
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


def model_is_babo_hosted_vllm(model: str | None) -> bool:
    """True for the Babo Cloud GX10 alias (vLLM upstream, not OpenRouter)."""
    return (model or "").strip().lower() == "babo-hosted"


def model_prefers_no_think(model: str | None) -> bool:
    """True for Qwen-family and Babo-hosted models that support /no_think."""
    m = (model or "").lower()
    return any(marker in m for marker in _QWEN_MODEL_MARKERS)


def inject_no_think(messages: list[dict]) -> list[dict]:
    """Prepend ``/no_think`` to the last user message (Qwen soft switch)."""
    msgs = [copy.copy(m) for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            content = msgs[i].get("content") or ""
            if not str(content).startswith("/no_think"):
                msgs[i] = {**msgs[i], "content": f"/no_think\n{content}"}
            break
    return msgs


def micro_inference_messages(
    messages: list[dict],
    *,
    model: str | None = None,
) -> list[dict]:
    """Prepare chat messages for short classifier / extraction calls."""
    if model_prefers_no_think(model):
        return inject_no_think(messages)
    return messages


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
    model: str | None = None,
) -> dict[str, Any]:
    """Request extras for short classifier / extraction micro-calls."""
    if thinking:
        return cloud_safe_extra_body(
            base_url,
            {},
            thinking=True,
            is_continuation=False,
        )

    extras: dict[str, Any] = {}

    # GX10 / local vLLM only — Babo Cloud OpenRouter models must not get template kwargs.
    if inference_host_is_local(base_url) or model_is_babo_hosted_vllm(model):
        extras["chat_template_kwargs"] = {"enable_thinking": False}
    elif not inference_host_is_local(base_url):
        extras["reasoning"] = {"effort": "none"}

    return _pass_through_micro_extras(base_url, extras, model=model)


def prepare_micro_inference(
    messages: list[dict],
    *,
    base_url: str = "",
    model: str | None = None,
    vllm_client: Any | None = None,
    adapter_name: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Return ``(messages, extra_body)`` for classifier / extraction micro-calls."""
    if vllm_client is not None:
        base_url = getattr(vllm_client, "base_url", "") or base_url
    resolved_model = (
        (model or "").strip()
        or (adapter_name or "").strip()
        or (
            str(getattr(vllm_client, "default_model", "") or "").strip()
            if vllm_client is not None
            else ""
        )
        or None
    )
    return (
        micro_inference_messages(messages, model=resolved_model),
        micro_inference_extra_body(
            base_url, thinking=False, model=resolved_model,
        ),
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


def _pass_through_micro_extras(
    base_url: str,
    extras: dict[str, Any],
    *,
    model: str | None,
) -> dict[str, Any]:
    """Keep micro-call extras that the upstream accepts."""
    if inference_host_is_local(base_url) or model_is_babo_hosted_vllm(model):
        skip = {
            "vllm_xargs",
            "continue_final_message",
            "add_generation_prompt",
        }
        return {k: v for k, v in extras.items() if k not in skip}

    skip = {
        "chat_template_kwargs",
        "vllm_xargs",
        "continue_final_message",
        "add_generation_prompt",
    }
    return {k: v for k, v in extras.items() if k not in skip}
