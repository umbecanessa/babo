#!/usr/bin/env python3
"""Compare OpenRouter models for reliable tool_calls via Babo Cloud relay."""
from __future__ import annotations

import json
import os
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

CANDIDATES = [
    "qwen/qwen3.6-35b-a3b",
    "qwen/qwen3-coder",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek/deepseek-v3.2",
    "openai/gpt-4o-mini",
    "anthropic/claude-sonnet-4",
    "meta-llama/llama-3.3-70b-instruct",
]


def probe(
    base_url: str,
    api_key: str,
    model: str,
    tool_choice: str,
) -> dict:
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Call the read tool with path /etc/hostname. "
                    "Output nothing else until the tool is called."
                ),
            }
        ],
        "tools": [READ_TOOL],
        "tool_choice": tool_choice,
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {
            "model": model,
            "tool_choice": tool_choice,
            "ok": False,
            "error": f"HTTP {e.code}: {e.read().decode()[:200]}",
        }
    except Exception as e:
        return {
            "model": model,
            "tool_choice": tool_choice,
            "ok": False,
            "error": str(e)[:200],
        }

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    tcs = msg.get("tool_calls") or []
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return {
        "model": model,
        "tool_choice": tool_choice,
        "ok": True,
        "finish": choice.get("finish_reason"),
        "n_tools": len(tcs),
        "tool_name": (tcs[0].get("function") or {}).get("name") if tcs else None,
        "reasoning_len": len(reasoning),
        "content_len": len(msg.get("content") or ""),
    }


def main() -> None:
    key = os.environ.get("PROBE_CLOUD_KEY", "").strip()
    if not key:
        cfg_path = os.path.expandvars(
            r"%APPDATA%\babo-desktop\nls-config.json"
        )
        if os.path.isfile(cfg_path):
            key = json.load(open(cfg_path, encoding="utf-8")).get(
                "inferenceApiKey", ""
            )
    if not key:
        print("Set PROBE_CLOUD_KEY or ensure nls-config.json has inferenceApiKey")
        raise SystemExit(1)

    base = os.environ.get(
        "PROBE_CLOUD_URL", "https://api.babo.agency/api/inference/v1"
    )
    print(f"Probing {base}\n")
    rows = []
    for model in CANDIDATES:
        for tc in ("auto", "required"):
            row = probe(base, key, model, tc)
            rows.append(row)
            status = "PASS" if row.get("n_tools") else "FAIL"
            if not row.get("ok"):
                status = "ERR"
            print(
                f"{status:4} {model:40} tool_choice={tc:8} "
                f"finish={row.get('finish', row.get('error', '?'))} "
                f"tools={row.get('n_tools', 0)} "
                f"reasoning={row.get('reasoning_len', '-')}"
            )

    auto_ok = [r["model"] for r in rows if r.get("tool_choice") == "auto" and r.get("n_tools")]
    req_only = [
        r["model"]
        for r in rows
        if r.get("tool_choice") == "required" and r.get("n_tools")
        and r["model"] not in auto_ok
    ]
    print("\n--- auto tool_calls OK ---")
    for m in auto_ok:
        print(" ", m)
    print("--- required-only (auto failed) ---")
    for m in req_only:
        print(" ", m)


if __name__ == "__main__":
    main()
