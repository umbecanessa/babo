#!/usr/bin/env python3
"""Probe vLLM / OpenRouter chat completions for tool_calls (local vs cloud)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}


def probe(base_url: str, model: str, *, enable_thinking: bool, api_key: str = "") -> None:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Use the read tool to read /etc/hostname. Do not explain; call the tool.",
            }
        ],
        "tools": [READ_TOOL],
        "tool_choice": "auto",
        "max_tokens": 512,
        "temperature": 0.3,
        "stream": False,
    }
    if enable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": True}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    print(f"\n=== {base_url} model={model} enable_thinking={enable_thinking} ===")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:800])
        return

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    print("finish_reason:", choice.get("finish_reason"))
    print("tool_calls:", json.dumps(msg.get("tool_calls"), indent=2)[:500])
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    print("content_len:", len(content), "preview:", content[:120])
    print("reasoning_len:", len(reasoning), "preview:", reasoning[:120])


def main() -> None:
    local_base = os.environ.get("PROBE_LOCAL_URL", "http://127.0.0.1:8000/v1")
    local_model = os.environ.get(
        "PROBE_LOCAL_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8"
    )
    cloud_base = os.environ.get(
        "PROBE_CLOUD_URL", "https://api.babo.agency/api/inference/v1"
    )
    cloud_model = os.environ.get("PROBE_CLOUD_MODEL", "qwen/qwen3.6-35b-a3b")
    cloud_key = os.environ.get("PROBE_CLOUD_KEY", "")

    probe(local_base, local_model, enable_thinking=True)
    probe(local_base, local_model, enable_thinking=False)
    if cloud_key:
        probe(cloud_base, cloud_model, enable_thinking=True, api_key=cloud_key)
        probe(cloud_base, cloud_model, enable_thinking=False, api_key=cloud_key)
    else:
        print("\n(set PROBE_CLOUD_KEY to compare Babo Cloud / OpenRouter)")


if __name__ == "__main__":
    main()
