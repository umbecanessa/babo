#!/usr/bin/env python3
"""Summarize OpenRouter token/cost lines from babo-desktop runtime.log."""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

LOG = Path(os.environ.get("APPDATA", "")) / "babo-desktop" / "runtime.log"
USAGE_RE = re.compile(r"usage=(\{.*?\})(?:\s*$|\s+completion)", re.MULTILINE)


def main() -> None:
    if not LOG.is_file():
        print(f"Missing {LOG}")
        return
    text = LOG.read_text(encoding="utf-8", errors="replace")
    rows: list[dict] = []
    for m in USAGE_RE.finditer(text):
        blob = m.group(1)
        try:
            u = ast.literal_eval(blob)
        except (SyntaxError, ValueError):
            continue
        if isinstance(u, dict) and "prompt_tokens" in u:
            rows.append(u)

    if not rows:
        print("No usage= blocks found in runtime.log")
        return

    tail = rows[-100:]
    pt = sum(int(r.get("prompt_tokens", 0)) for r in tail)
    ct = sum(int(r.get("completion_tokens", 0)) for r in tail)
    cost = sum(float(r.get("cost", 0) or 0) for r in tail)
    mx = max(int(r.get("prompt_tokens", 0)) for r in tail)

    print(f"Log: {LOG}")
    print(f"Last {len(tail)} inference calls with usage metadata")
    print(f"  Prompt tokens:     {pt:,}")
    print(f"  Completion tokens: {ct:,}")
    print(f"  Total tokens:      {pt + ct:,}")
    print(f"  Sum cost (USD):    ${cost:.4f}")
    print(f"  Max prompt/call:   {mx:,}")
    print(f"  Avg prompt/call:   {pt // len(tail):,}")


if __name__ == "__main__":
    main()
