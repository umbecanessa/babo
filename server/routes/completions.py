"""OpenAI-Compatible Chat Completions endpoint.

Implements ``POST /v1/chat/completions`` following the OpenAI API
format, enabling use with the standard ``openai`` Python SDK::

    from openai import OpenAI
    client = OpenAI(
        base_url="https://api.openai.com/v1",
        api_key="nlsk_abc123..."
    )
    response = client.chat.completions.create(
        model="agent:550e8400-...",
        messages=[{"role": "user", "content": "Hello!"}],
        stream=True,
    )
    for chunk in response:
        print(chunk.choices[0].delta.content, end="")

The ``model`` field maps to an agent:
    - ``"agent:{agent_id}"`` -- specific agent
    - ``"babo-8b"`` -- uses the default genesis / last active agent

NLS-specific metadata is included in the response under an ``nls``
key (signals, hormones, agent state).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["openai-compat"])


# --- Request / Response Models (OpenAI format) ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = Field(
        ..., description="Model or 'agent:{agent_id}'",
    )
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    memory_test_mode: bool = False
    no_deltanet: bool = False


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class NLSMetadata(BaseModel):
    signals: list[dict[str, Any]] = Field(default_factory=list)
    agent_state: str = "awake"
    hormones: dict[str, float] = Field(default_factory=dict)
    facts_in_memory: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)
    nls: NLSMetadata | None = None
    thinking: str | None = None


# --- Endpoints ---


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    """OpenAI-compatible chat completion endpoint.

    Supports both streaming (SSE) and non-streaming modes.
    """
    agent_id = _resolve_agent_id(body.model, request)

    # Build history from messages (exclude last user message)
    history = [
        {"role": m.role, "content": m.content}
        for m in body.messages[:-1]
    ]
    user_input = body.messages[-1].content if body.messages else ""

    if not user_input:
        raise HTTPException(
            status_code=400, detail="No user message provided",
        )

    if body.stream:
        return StreamingResponse(
            _stream_response(agent_id, user_input, history, body, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming
    _runtime = request.app.state.agent_manager.get_runtime(agent_id)
    if _runtime is not None:
        _runtime._max_tokens_override = body.max_tokens

    result = await request.app.state.agent_manager.process_message(
        agent_id=agent_id,
        user_input=user_input,
        history=history,
        memory_test_mode=body.memory_test_mode,
        no_deltanet=body.no_deltanet,
    )

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    _pt = result.get("prompt_tokens", 0)
    _ct = result.get("completion_tokens", 0)

    return ChatCompletionResponse(
        id=completion_id,
        created=int(time.time()),
        model=body.model,
        choices=[
            Choice(
                message=ChatMessage(
                    role="assistant",
                    content=result.get("response", ""),
                ),
            ),
        ],
        usage=Usage(
            prompt_tokens=_pt,
            completion_tokens=_ct,
            total_tokens=_pt + _ct,
        ),
        nls=NLSMetadata(
            signals=result.get("signals", []),
            agent_state="awake",
            hormones=result.get("hormones", {}),
            facts_in_memory=result.get("facts_in_memory", 0),
        ),
        thinking=result.get("thinking") or None,
    )


async def _stream_response(
    agent_id: str,
    user_input: str,
    history: list[dict],
    body: ChatCompletionRequest,
    request: Request,
):
    """Generate SSE stream in OpenAI format — truly real-time.

    Yields ``data: {...}`` chunks as tokens arrive from vLLM.
    Thinking tokens are sent as ``reasoning_content`` in the delta.
    """
    import asyncio

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    agent_manager = request.app.state.agent_manager
    if agent_id not in agent_manager._runtimes:
        await agent_manager.load_agent(agent_id)

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        yield _sse_error("Agent runtime not available")
        return

    queue: asyncio.Queue = asyncio.Queue()

    async def _producer():
        try:
            runtime._max_tokens_override = body.max_tokens
            async for token in runtime.process_message_stream_async(
                user_input, history,
                memory_test_mode=body.memory_test_mode,
                no_deltanet=body.no_deltanet,
            ):
                await queue.put(token)
        except Exception as exc:
            await queue.put(("error", str(exc)))
        finally:
            runtime._max_tokens_override = None
            await queue.put(None)

    task = asyncio.create_task(_producer())

    while True:
        item = await queue.get()
        if item is None:
            break

        if isinstance(item, tuple):
            kind, text = item
            if kind == "thinking":
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body.model,
                    "choices": [{
                        "index": 0,
                        "delta": {"reasoning_content": text},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            elif kind == "thinking_end":
                pass
            elif kind == "error":
                yield _sse_error(text)
                break
        else:
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": body.model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": item},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(chunk)}\n\n"

    await task

    _turn_result = getattr(runtime, "last_stream_turn_result", None)
    _usage = {}
    if _turn_result is not None:
        _pt = getattr(_turn_result, "prompt_tokens", 0)
        _ct = getattr(_turn_result, "completion_tokens", 0)
        _usage = {
            "prompt_tokens": _pt,
            "completion_tokens": _ct,
            "total_tokens": _pt + _ct,
        }

    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": body.model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    if _usage:
        final["usage"] = _usage
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@router.get("/models")
async def list_models(request: Request):
    """OpenAI-compatible model listing.

    Lists available genesis templates as models, plus any active agents
    as ``agent:{id}`` models.
    """
    settings = request.app.state.settings
    models = []

    # Base model
    models.append({
        "id": "babo-8b",
        "object": "model",
        "created": int(time.time()),
        "owned_by": "nls",
    })

    # Genesis templates
    if settings.genesis_dir.exists():
        for d in sorted(settings.genesis_dir.iterdir()):
            if d.is_dir() and (d / "manifest.json").exists():
                models.append({
                    "id": f"genesis:{d.name}",
                    "object": "model",
                    "created": int(d.stat().st_mtime),
                    "owned_by": "nls",
                })

    # Active agents
    for agent_id in request.app.state.agent_manager._runtimes:
        models.append({
            "id": f"agent:{agent_id}",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "nls",
        })

    return {"object": "list", "data": models}


# --- Helpers ---


def _resolve_agent_id(model: str, request: Request) -> str:
    """Resolve model string to an agent_id.

    - ``"agent:UUID"`` -> UUID
    - ``"babo-8b"`` / anything else -> first active agent or error
    """
    if model.startswith("agent:"):
        return model[6:]

    # Try to find a default active agent
    runtimes = request.app.state.agent_manager._runtimes
    if runtimes:
        return next(iter(runtimes))

    raise HTTPException(
        status_code=400,
        detail=f"Model '{model}' not found. Use 'agent:{{agent_id}}' "
        f"or create an agent first.",
    )


def _sse_error(message: str) -> str:
    """Format an error as SSE."""
    return f"data: {json.dumps({'error': message})}\n\n"
