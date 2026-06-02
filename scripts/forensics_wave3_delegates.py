#!/usr/bin/env python3
"""Deep dive on Wave 3 delegate loops #4 and #5."""
import json
import glob
import os
import sys
from collections import Counter, defaultdict

AGENT = sys.argv[1] if len(sys.argv) > 1 else "."


def analyze_loop(path: str) -> dict | None:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not events or events[0].get("event") != "loop_start":
        return None
    start = events[0]
    end = next((e for e in reversed(events) if e.get("event") == "loop_end"), None)
    tools = Counter()
    fails = []
    bash_cmds = []
    reads = []
    edits = []
    writes = []
    hints_in_context = 0
    for e in events:
        if e.get("event") == "tool_result":
            name = e.get("tool", "?")
            ok = e.get("success", False)
            tools[(name, "ok" if ok else "fail")] += 1
            prev = (e.get("content_preview") or "")[:200]
            if not ok:
                fails.append((name, prev))
            if name == "bash":
                # find matching generation in same iteration
                pass
        if e.get("event") == "generation":
            for tc in e.get("tool_calls") or []:
                n = tc.get("name", "")
                args = tc.get("args_preview", "")
                if n == "bash":
                    bash_cmds.append(args[:120])
                elif n == "read":
                    reads.append(args[:100])
                elif n == "edit":
                    edits.append(args[:100])
                elif n == "write":
                    writes.append(args[:100])
        if e.get("event") == "iteration_start":
            pass
    # scan user messages in journal not here
    for e in events:
        if e.get("event") == "loop_start":
            ui = start.get("user_input_preview", "")
            if "hint" in ui.lower() or "CRITICAL" in ui:
                hints_in_context += 1
    return {
        "file": os.path.basename(path),
        "loop_id": start.get("loop_id", "")[:8],
        "start": (start.get("_ts") or "")[:19],
        "goals": start.get("goals", [])[:3],
        "iters": end.get("iterations", 0) if end else max(
            e.get("iteration", 0) for e in events if e.get("event") == "iteration_start"
        ),
        "dur": end.get("duration_s", 0) if end else 0,
        "exit": end.get("exit_reason", "INCOMPLETE") if end else "INCOMPLETE",
        "tc": end.get("total_tool_calls", 0) if end else sum(1 for e in events if e.get("event") == "tool_result"),
        "prompt_tok": end.get("total_prompt_tokens", 0) if end else 0,
        "compl_tok": end.get("total_completion_tokens", 0) if end else 0,
        "tools": dict(tools),
        "fails": fails[:25],
        "bash_cmds": bash_cmds[:15],
        "reads": reads[:15],
        "edits": edits[:20],
        "writes": writes[:15],
        "incomplete": end is None,
    }


def main() -> None:
    base = os.path.join(AGENT, "agentic_logs")
    # delegate loops often have goals about Assembly AI or Anthropic
    wave3 = []
    for path in sorted(glob.glob(os.path.join(base, "loop_*.jsonl"))):
        info = analyze_loop(path)
        if not info:
            continue
        preview = ""
        with open(path, encoding="utf-8") as f:
            first = json.loads(f.readline())
            preview = first.get("user_input_preview", "") + " ".join(first.get("goals", []))
        if any(
            k in preview.lower()
            for k in ("assembly", "transcription", "anthropic", "icf marker", "wave 3")
        ):
            wave3.append(info)

    print(f"=== WAVE 3 DELEGATE LOOPS: {len(wave3)} ===\n")
    for info in wave3:
        print(f"--- {info['file']} loop={info['loop_id']} start={info['start']} ---")
        print(f"  exit={info['exit']} iters={info['iters']} dur={info['dur']:.0f}s tc={info['tc']}")
        print(f"  tokens: prompt={info['prompt_tok']:,} completion={info['compl_tok']:,}")
        print(f"  tools: {info['tools']}")
        if info["bash_cmds"]:
            print(f"  bash samples ({len(info['bash_cmds'])}):")
            for b in info["bash_cmds"][:8]:
                print(f"    {b}")
        if info["reads"]:
            print(f"  read samples:")
            for r in info["reads"][:6]:
                print(f"    {r}")
        if info["edits"]:
            print(f"  edit samples ({len(info['edits'])}):")
            for r in info["edits"][:10]:
                print(f"    {r}")
        if info["writes"]:
            print(f"  write samples:")
            for r in info["writes"][:6]:
                print(f"    {r}")
        if info["fails"]:
            print(f"  failures ({len(info['fails'])}):")
            for name, prev in info["fails"][:12]:
                print(f"    [{name}] {prev}")
        print()

    # orchestrator hints
    print("=== ORCHESTRATOR HINTS (team hint) ===")
    for path in sorted(glob.glob(os.path.join(base, "loop_*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "hint" not in line.lower():
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("event") == "generation":
                    for tc in e.get("tool_calls") or []:
                        if tc.get("name") == "team" and "hint" in tc.get("args_preview", ""):
                            print((tc.get("args_preview") or "")[:400])
                            print()


if __name__ == "__main__":
    main()
