"""OpenAI-compatible inference client for Babo."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class GenerateResult:
    """Result from a non-streaming generate call."""

    text: str
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    logprobs: list[dict[str, Any]] | None = None
    latency_ms: float = 0.0
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class AdapterInfo:
    """Metadata for a loaded inference adapter alias."""

    name: str
    path: str
    loaded_at: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════
# vLLM Inference Client
# ═══════════════════════════════════════════════════════════════════════════


class VLLMInferenceClient:
    """Async client for the vLLM OpenAI-compatible API.

    Parameters
    ----------
    base_url : str
        vLLM server URL (default ``http://localhost:8000``).
    default_model : str
        Model name when no adapter is specified.
    timeout : float
        HTTP request timeout in seconds.
    max_retries : int
        Number of retries on transient failures.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        default_model: str = "gpt-4o-mini",
        timeout: float = 180.0,
        max_retries: int = 2,
        max_concurrent: int = 6,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._api_key = api_key or ""
        self._model_auto_detected = False
        self.timeout = timeout
        self.max_retries = max_retries

        # Streaming requests need a much longer read timeout than normal
        # RPCs.  During prefill of large contexts (60-80K tokens), vLLM
        # sends NO SSE events — the httpx read timeout fires if prefill
        # takes longer than `timeout`.  With multiple concurrent requests
        # sharing the GPU this routinely exceeds 180s.  The agentic loop's
        # wall-clock timeout already provides a hard upper bound, so we
        # use a generous 600s here to avoid premature ReadTimeout kills.
        self._stream_timeout = httpx.Timeout(
            read=600.0, connect=10.0, write=30.0, pool=60.0,
        )

        # Concurrency safety valve — prevents GPU starvation when many
        # agentic loops (orchestrator + sub-agents + channels + dreams)
        # fire requests simultaneously.  vLLM handles batching, but
        # extreme parallelism (10+ concurrent) degrades all latencies.
        import asyncio
        self._concurrency = asyncio.Semaphore(max_concurrent)

        # Structured tool calls from the last streaming generation
        self.last_stream_tool_calls: list[dict[str, Any]] | None = None
        self.last_stream_finish_reason: str = "stop"
        self.last_stream_usage: dict[str, int] = {}

        # Persistent async HTTP client with connection pooling.
        # Stored kwargs so we can recreate if the client gets closed.
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client_kwargs = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(timeout, connect=10.0),
            "limits": httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            "headers": headers,
        }
        self._client = httpx.AsyncClient(**self._client_kwargs)

        # Track loaded adapters
        self._loaded_adapters: dict[str, AdapterInfo] = {}

    # ===================================================================
    # Client Self-Healing
    # ===================================================================

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the httpx client, recreating it if it was closed.

        During server shutdown the client gets explicitly closed via
        ``close()`` / ``aclose()``.  If an in-flight async loop still
        holds a reference and retries, the closed client raises
        ``RuntimeError: Cannot send a request``.  Recreating the client
        lets transient-retry logic succeed when the underlying vLLM
        server is still reachable.
        """
        if self._client.is_closed:
            logger.warning(
                "httpx client was closed — recreating connection pool "
                "(base_url=%s)", self.base_url,
            )
            self._client = httpx.AsyncClient(**self._client_kwargs)
        return self._client

    def set_cache_request_tag(self, agent_id: str) -> None:
        """Optional request tag for provider-side caching (no-op by default)."""
        _ = agent_id

    def _inject_router_bias(self, body: dict[str, Any]) -> None:
        """No-op — product runtime does not inject router bias into requests."""
        _ = body

    # ===================================================================
    # Health Check
    # ===================================================================

    async def health_check(self) -> bool:
        """Check if the vLLM server is healthy.

        Returns True if the server is responding.
        """
        try:
            client = self._ensure_client()
            resp = await client.get("/health", timeout=5.0)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    async def wait_until_ready(
        self,
        timeout: float = 300.0,
        poll_interval: float = 5.0,
    ) -> bool:
        """Block until the vLLM server is ready, with timeout.

        Returns True if the server became ready, False on timeout.
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.health_check():
                logger.info(
                    "vLLM server ready at %s (%.1fs)",
                    self.base_url, time.time() - t0,
                )
                await self._auto_detect_model()
                return True
            await _async_sleep(poll_interval)
        logger.error(
            "vLLM server at %s not ready after %.0fs",
            self.base_url, timeout,
        )
        return False

    async def _auto_detect_model(self) -> None:
        """Query vLLM /v1/models and override default_model if needed."""
        if self._model_auto_detected:
            return
        try:
            resp = await self._ensure_client().get("/v1/models", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    served = models[0].get("id", "")
                    if served and served != self.default_model:
                        logger.info(
                            "vLLM serves model %r (overriding configured %r)",
                            served, self.default_model,
                        )
                        self.default_model = served
                    self._model_auto_detected = True
        except Exception as exc:
            logger.debug("Model auto-detect failed (non-fatal): %s", exc)

    # ===================================================================
    # Tokenization (via vLLM /tokenize endpoint)
    # ===================================================================

    async def count_tokens_for_chat(
        self,
        messages: list[dict[str, str]],
    ) -> int:
        """Count tokens for a chat message list using vLLM's tokenizer.

        Calls the /tokenize endpoint which applies the model's chat
        template, giving exact token counts including special tokens.
        Returns 0 on any error (non-critical, caller falls back to heuristic).
        """
        client = self._ensure_client()
        try:
            resp = await client.post(
                f"{self.base_url}/tokenize",
                json={
                    "model": self.default_model,
                    "messages": messages,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("count", 0)
                if not count:
                    tokens = data.get("tokens", [])
                    count = len(tokens)
                return count
            logger.debug("vLLM /tokenize returned %d", resp.status_code)
        except Exception as exc:
            logger.debug("vLLM /tokenize failed (non-critical): %s", exc)
        return 0

    # ===================================================================
    # Non-Streaming Generation
    # ===================================================================

    async def generate(
        self,
        prompt: str | None = None,
        adapter_name: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        logprobs: int | None = None,
        stop: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> GenerateResult:
        """Generate a completion (non-streaming).

        Uses ``/v1/chat/completions`` when *messages* is provided
        (preferred -- works with vLLM v1 engine).  Falls back to
        ``/v1/completions`` when only *prompt* is given (legacy path,
        requires vLLM v0 engine).

        Parameters
        ----------
        prompt : str | None
            The formatted prompt text (legacy path).
        adapter_name : str | None
            Optional model alias.  When provided, maps to the ``model``
            field in the vLLM request.  When ``None``, uses the base model.
        messages : list of dict, optional
            Chat messages (preferred).  Each dict has ``role`` and
            ``content`` keys.  When provided, uses ``/v1/chat/completions``.
        max_tokens : int
            Maximum tokens to generate.
        temperature : float
            Sampling temperature.
        top_p : float
            Nucleus sampling probability.
        logprobs : int | None
            Number of logprobs to return per token (for Inference
            Interceptor).  ``None`` disables logprobs.
        stop : list of str, optional
            Stop sequences.
        tools : list of dict, optional
            OpenAI-format tool definitions.  When provided (and using
            ``/v1/chat/completions``), the model may return structured
            ``tool_calls`` instead of plain text.
        tool_choice : str or dict, optional
            Tool selection strategy: ``"auto"`` (default when tools are
            provided), ``"required"``, ``"none"``, or a named function.
        extra_body : dict, optional
            Additional request body fields.
        """
        model = adapter_name if adapter_name else self.default_model
        use_chat = messages is not None

        if use_chat:
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": False,
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = tool_choice or "auto"
        else:
            if prompt is None:
                raise ValueError("Either 'prompt' or 'messages' is required")
            body = {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": False,
            }

        if logprobs is not None:
            body["logprobs"] = logprobs

        if stop:
            body["stop"] = stop

        if extra_body:
            body.update(extra_body)

        self._inject_router_bias(body)

        # DEBUG: dump request body (temporary)
        import json as _djson
        _xargs = body.get("vllm_xargs", {})
        _rb = _xargs.get("router_bias", "")
        _rb_summary = f"len={len(_rb)}" if isinstance(_rb, str) else repr(_rb)[:100]
        _msgs = body.get("messages", [])
        _msg_summary = []
        for _m in _msgs:
            _role = _m.get("role", "?")
            _content = _m.get("content") or ""
            if isinstance(_content, list):
                # multimodal content — count text chars + note image presence
                _text_len = sum(
                    len(p.get("text", ""))
                    for p in _content if isinstance(p, dict) and p.get("type") == "text"
                )
                _has_img = any(
                    isinstance(p, dict) and p.get("type") == "image_url"
                    for p in _content
                )
                _msg_summary.append(
                    f"{_role}:{_text_len}chars+img" if _has_img
                    else f"{_role}:{_text_len}chars"
                )
            else:
                _msg_summary.append(f"{_role}:{len(_content)}chars")
        logger.info(
            "vLLM request: model=%s max_tokens=%s temp=%s "
            "has_tools=%s has_vllm_xargs=%s router_bias=%s "
            "xargs_keys=%s msgs=%s",
            body.get("model", "?"),
            body.get("max_tokens"),
            body.get("temperature"),
            "tools" in body,
            bool(_xargs),
            _rb_summary,
            list(_xargs.keys()) if _xargs else [],
            _msg_summary,
        )
        # Dump FULL request body for exact replay (temporary)
        try:
            import time as _t
            _dump_path = f"/tmp/nls_request_{int(_t.time()*1000)}.json"
            with open(_dump_path, "w") as _f:
                _djson.dump(body, _f, indent=2, default=str)
            with open("/tmp/nls_last_request.json", "w") as _f:
                _djson.dump(body, _f, indent=2, default=str)
        except Exception:
            pass

        t0 = time.perf_counter()

        endpoint = "/v1/chat/completions" if use_chat else "/v1/completions"
        resp = await self._request_with_retry(
            "POST", endpoint, json=body,
        )

        latency_ms = (time.perf_counter() - t0) * 1000

        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})

        # Extract generated text -- format differs between APIs
        if use_chat:
            message = choice.get("message", {})
            text = message.get("content") or ""
            # vLLM >=0.17 uses "reasoning" field; older versions used
            # "reasoning_content".  Check both for compatibility.
            reasoning = (
                message.get("reasoning")
                or message.get("reasoning_content")
                or ""
            )
            if reasoning:
                text = f"<think>{reasoning}</think>{text}"
            raw_tool_calls = message.get("tool_calls")
            logger.info(
                "vLLM raw response: finish=%s content_len=%d "
                "reasoning_len=%d tool_calls=%s usage=%s",
                choice.get("finish_reason"),
                len(message.get("content") or ""),
                len(reasoning),
                bool(raw_tool_calls),
                usage,
            )
        else:
            text = choice.get("text", "")
            raw_tool_calls = None

        # Extract logprobs if present
        token_logprobs = None
        if logprobs is not None and "logprobs" in choice:
            lp_data = choice["logprobs"]
            if isinstance(lp_data, dict):
                token_logprobs = lp_data.get("token_logprobs")
            elif isinstance(lp_data, list):
                # chat completions may return list of {token, logprob, ...}
                token_logprobs = [
                    item.get("logprob") for item in lp_data
                ]
            if token_logprobs is not None:
                # Wrap in dicts for compatibility with Inference Interceptor
                token_logprobs = [
                    {"logprob": lp} for lp in token_logprobs
                ]

        return GenerateResult(
            text=text,
            finish_reason=choice.get("finish_reason", "stop"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            logprobs=token_logprobs,
            latency_ms=latency_ms,
            tool_calls=raw_tool_calls,
        )

    # ===================================================================
    # Streaming Generation
    # ===================================================================

    async def generate_stream(
        self,
        prompt: str | None = None,
        adapter_name: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stop: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        yield_tool_deltas: bool = False,
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Stream tokens as they are generated.

        Yields individual token strings via Server-Sent Events (SSE).

        Uses ``/v1/chat/completions`` when *messages* is provided
        (preferred).  Falls back to ``/v1/completions`` with *prompt*.

        When ``tools`` are provided, the model may respond with
        structured tool calls instead of (or in addition to) text.
        Tool call deltas are accumulated internally and stored in
        ``self.last_stream_tool_calls`` after the stream completes.

        Parameters
        ----------
        prompt : str | None
            The formatted prompt text (legacy path).
        adapter_name : str | None
            Optional model alias (maps to ``model`` field).
        messages : list of dict, optional
            Chat messages (preferred).
        max_tokens : int
            Maximum tokens to generate.
        temperature : float
            Sampling temperature.
        top_p : float
            Nucleus sampling probability.
        stop : list of str, optional
            Stop sequences.
        tools : list of dict, optional
            OpenAI-format tool definitions for structured tool calling.
        tool_choice : str or dict, optional
            Tool selection strategy (``"auto"``, ``"required"``, etc.).
        extra_body : dict, optional
            Additional request body fields.
        """
        model = adapter_name if adapter_name else self.default_model
        use_chat = messages is not None

        if use_chat:
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = tool_choice or "auto"
        else:
            if prompt is None:
                raise ValueError("Either 'prompt' or 'messages' is required")
            body = {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": True,
                "stream_options": {"include_usage": True},
            }

        if stop:
            body["stop"] = stop

        if extra_body:
            body.update(extra_body)

        self._inject_router_bias(body)

        endpoint = "/v1/chat/completions" if use_chat else "/v1/completions"

        # Reset accumulated tool calls from any previous stream
        self.last_stream_tool_calls: list[dict[str, Any]] | None = None
        accumulated_tc: dict[int, dict[str, Any]] = {}
        self.last_stream_finish_reason: str = "stop"
        self.last_stream_usage = {}
        _in_reasoning = False  # tracks <think> wrapper for reasoning_content

        # Acquire concurrency permit before hitting vLLM
        await self._concurrency.acquire()

        # Retry wrapper for transient connection failures (DNS, TCP reset,
        # mid-stream ReadError from vLLM drops or client-closed races).
        _stream_ctx = None
        try:
            for _attempt in range(1 + self.max_retries):
                try:
                    client = self._ensure_client()
                    _stream_ctx = client.stream(
                        "POST",
                        endpoint,
                        json=body,
                        timeout=self._stream_timeout,
                    )
                    response = await _stream_ctx.__aenter__()
                    if response.status_code == 400:
                        body_text = (await response.aread()).decode(errors="replace")[:2000]
                        await _stream_ctx.__aexit__(None, None, None)
                        _stream_ctx = None
                        raise httpx.HTTPStatusError(
                            f"400 Bad Request: {body_text}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    break
                except (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.ReadError,
                    RuntimeError,
                    OSError,
                ) as exc:
                    if _stream_ctx is not None:
                        try:
                            await _stream_ctx.__aexit__(type(exc), exc, exc.__traceback__)
                        except Exception:
                            pass
                        _stream_ctx = None
                    if _attempt >= self.max_retries:
                        raise
                    _wait = 2 ** _attempt
                    logger.warning(
                        "vLLM stream connect failed (%s: %s), retrying in %ds "
                        "(attempt %d/%d)",
                        type(exc).__name__, exc, _wait,
                        _attempt + 1, self.max_retries + 1,
                    )
                    await _async_sleep(_wait)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]  # Strip "data: " prefix

                if data_str.strip() == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)

                    usage = data.get("usage")
                    if usage:
                        self.last_stream_usage = {
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    finish = choice.get("finish_reason")
                    if finish:
                        self.last_stream_finish_reason = finish

                    if use_chat:
                        delta = choice.get("delta", {})
                        text = delta.get("content") or ""

                        # vLLM >=0.17 uses "reasoning" field; older
                        # versions used "reasoning_content".  Wrap it
                        # in <think> tags so the rest of the pipeline
                        # (frontend, strip_thinking) works unchanged.
                        reasoning_chunk = (
                            delta.get("reasoning")
                            or delta.get("reasoning_content")
                            or ""
                        )
                        delta_tcs = delta.get("tool_calls")
                        if reasoning_chunk:
                            if not _in_reasoning:
                                _in_reasoning = True
                                reasoning_chunk = "<think>" + reasoning_chunk
                        elif _in_reasoning and (text or finish or delta_tcs):
                            _in_reasoning = False
                            text = "</think>" + text

                        if reasoning_chunk:
                            yield reasoning_chunk

                        # Accumulate streaming tool call deltas
                        if delta_tcs:
                            for tc_delta in delta_tcs:
                                idx = tc_delta.get("index", 0)
                                if idx not in accumulated_tc:
                                    accumulated_tc[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "type": "function",
                                        "function": {
                                            "name": "",
                                            "arguments": "",
                                        },
                                    }
                                entry = accumulated_tc[idx]
                                if tc_delta.get("id"):
                                    entry["id"] = tc_delta["id"]
                                func_delta = tc_delta.get("function", {})
                                if func_delta.get("name"):
                                    entry["function"]["name"] = func_delta["name"]
                                args_chunk = func_delta.get("arguments", "")
                                if args_chunk:
                                    entry["function"]["arguments"] += args_chunk
                                    if yield_tool_deltas:
                                        yield {
                                            "type": "tool_delta",
                                            "index": idx,
                                            "function_name": entry["function"]["name"],
                                            "arguments_delta": args_chunk,
                                        }
                    else:
                        text = choice.get("text", "")

                    if text:
                        yield text
                except json.JSONDecodeError:
                    logger.warning("Failed to parse SSE data: %s", data_str)
        finally:
            if _stream_ctx is not None:
                await _stream_ctx.__aexit__(None, None, None)
            self._concurrency.release()

        # Close any unclosed <think> tag from reasoning_content
        if _in_reasoning:
            yield "</think>"
            _in_reasoning = False

        # Store accumulated tool calls (if any) for the caller
        if accumulated_tc:
            valid_calls = []
            for i in sorted(accumulated_tc):
                tc = accumulated_tc[i]
                args = tc.get("function", {}).get("arguments", "")
                if args:
                    try:
                        json.loads(args)
                        valid_calls.append(tc)
                    except json.JSONDecodeError:
                        # Salvage: model emitted raw value without JSON wrapper.
                        fn_name = tc.get("function", {}).get("name", "")
                        salvaged = False
                        if fn_name == "bash":
                            tc["function"]["arguments"] = json.dumps(
                                {"command": args.strip()}
                            )
                            salvaged = True
                        elif fn_name:
                            param_key = "input"
                            if tools:
                                for ts in tools:
                                    if ts.get("function", {}).get("name") == fn_name:
                                        req = (
                                            ts.get("function", {})
                                            .get("parameters", {})
                                            .get("required", [])
                                        )
                                        if req:
                                            param_key = req[0]
                                        break
                            tc["function"]["arguments"] = json.dumps(
                                {param_key: args.strip()}
                            )
                            salvaged = True
                        if salvaged:
                            logger.info(
                                "Salvaged non-JSON tool call %s (raw args → JSON)",
                                fn_name,
                            )
                            valid_calls.append(tc)
                        else:
                            logger.warning(
                                "Dropping tool call %s — unparseable arguments: %s",
                                fn_name or "?",
                                args[-80:],
                            )
                else:
                    valid_calls.append(tc)
            self.last_stream_tool_calls = valid_calls
            logger.info(
                "Stream completed with %d structured tool call(s)",
                len(self.last_stream_tool_calls),
            )

    # ===================================================================
    # Adapter Management (Hot-Reload)
    # ===================================================================

    async def load_adapter(
        self,
        name: str,
        path: str,
        load_inplace: bool = False,
    ) -> None:
        """No-op in Babo product mode (adapter hot-load removed)."""
        logger.debug(
            "load_adapter ignored (product mode): name=%s path=%s", name, path,
        )

    async def unload_adapter(self, name: str) -> None:
        """No-op in Babo product mode (adapter hot-load removed)."""
        logger.debug("unload_adapter ignored (product mode): name=%s", name)

    async def list_models(self) -> list[str]:
        """List available models (base + loaded adapters) from vLLM."""
        resp = await self._ensure_client().get("/v1/models")
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    # ===================================================================
    # Cleanup
    # ===================================================================

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if not self._client.is_closed:
            await self._client.aclose()
        logger.info("VLLMInferenceClient closed")

    # ===================================================================
    # Status / Introspection
    # ===================================================================

    def get_status(self) -> dict[str, Any]:
        """Return client status for health endpoint."""
        return {
            "base_url": self.base_url,
            "default_model": self.default_model,
            "loaded_adapters": {
                name: {"path": info.path, "loaded_at": info.loaded_at}
                for name, info in self._loaded_adapters.items()
            },
            "adapter_count": len(self._loaded_adapters),
        }

    # ===================================================================
    # Internal: Retry Logic
    # ===================================================================

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an HTTP request with retry on transient failures.

        Retries on:
        - Connection errors
        - 503 Service Unavailable (vLLM warming up)
        - 429 Too Many Requests
        """
        last_exc: Exception | None = None

        await self._concurrency.acquire()
        try:
            for attempt in range(1 + self.max_retries):
                try:
                    client = self._ensure_client()
                    resp = await client.request(method, url, **kwargs)

                    if resp.status_code in (429, 503):
                        wait = 2 ** attempt
                        logger.warning(
                            "vLLM %s %s returned %d, retrying in %ds "
                            "(attempt %d/%d)",
                            method, url, resp.status_code, wait,
                            attempt + 1, self.max_retries + 1,
                        )
                        await _async_sleep(wait)
                        continue

                    if resp.status_code == 400:
                        body_text = resp.text[:2000]
                        logger.error(
                            "vLLM %s %s returned 400 Bad Request: %s",
                            method, url, body_text,
                        )
                        if "tool" in body_text.lower() and "auto" in body_text.lower():
                            req_body = kwargs.get("json", {})
                            if "tools" in req_body or "tool_choice" in req_body:
                                logger.warning(
                                    "vLLM does not support tool_choice; "
                                    "retrying without tools",
                                )
                                self._tool_choice_supported = False
                                req_body.pop("tools", None)
                                req_body.pop("tool_choice", None)
                                continue
                    resp.raise_for_status()
                    return resp

                except (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.ReadError,
                    RuntimeError,
                ) as exc:
                    last_exc = exc
                    wait = 2 ** attempt
                    logger.warning(
                        "vLLM %s %s failed (%s), retrying in %ds "
                        "(attempt %d/%d)",
                        method, url, type(exc).__name__, wait,
                        attempt + 1, self.max_retries + 1,
                    )
                    await _async_sleep(wait)

            raise ConnectionError(
                f"vLLM server at {self.base_url} unreachable after "
                f"{self.max_retries + 1} attempts"
            ) from last_exc
        finally:
            self._concurrency.release()


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _async_sleep(seconds: float) -> None:
    """Wrapper for asyncio.sleep (allows easy mocking in tests)."""
    import asyncio
    await asyncio.sleep(seconds)
