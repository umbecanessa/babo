#!/usr/bin/env python3
"""Quick forensics on an agent's agentic_logs."""
import json
import glob
import os
import sys
from collections import Counter, defaultdict

def main(agent_dir: str) -> None:
    base = os.path.join(agent_dir, "agentic_logs")
    loops = sorted(glob.glob(os.path.join(base, "loop_*.jsonl")))
    print("=== LOOP FILES ===", len(loops))

    summaries = []
    tool_stats = Counter()
    errors = Counter()
    exit_reasons = Counter()
    sources = Counter()
    token_total = {"prompt": 0, "completion": 0}

    for path in loops:
        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not events:
            continue
        start = events[0]
        if start.get("event") != "loop_start":
            continue
        end = next((e for e in reversed(events) if e.get("event") == "loop_end"), None)
        src = start.get("dispatch_source", "?")
        sources[src] += 1
        iters = end.get("iterations", 0) if end else 0
        dur = end.get("duration_s", 0) if end else 0
        exit_r = end.get("exit_reason", "INCOMPLETE") if end else "INCOMPLETE"
        exit_reasons[exit_r] += 1
        tc = end.get("total_tool_calls", 0) if end else 0
        if end:
            token_total["prompt"] += end.get("total_prompt_tokens", 0) or 0
            token_total["completion"] += end.get("total_completion_tokens", 0) or 0
            for t, c in (end.get("tool_errors") or {}).items():
                tool_stats[(t, "err")] += c
            for t, c in (end.get("tool_successes") or {}).items():
                tool_stats[(t, "ok")] += c
        loop_tool_err = 0
        loop_tools = 0
        max_iter = 0
        for e in events:
            if e.get("event") == "tool_result":
                loop_tools += 1
                if not e.get("success"):
                    loop_tool_err += 1
                    errors[(e.get("content_preview") or "")[:100]] += 1
            if e.get("event") == "iteration_start":
                max_iter = max(max_iter, e.get("iteration", 0))
            if e.get("event") == "generation" and e.get("error"):
                errors[str(e.get("error"))[:100]] += 1
        summaries.append({
            "file": os.path.basename(path),
            "loop_id": (start.get("loop_id") or "?")[:8],
            "source": src[:55],
            "start": (start.get("_ts") or "")[:19],
            "iters": iters or max_iter,
            "dur_s": dur,
            "exit": exit_r,
            "tc": tc,
            "tool_events": loop_tools,
            "tool_err": loop_tool_err,
            "incomplete": end is None,
        })

    print("\n=== BY DISPATCH SOURCE ===")
    for s, n in sources.most_common():
        print(f"  {n:3d}  {s}")

    print("\n=== EXIT REASONS ===")
    for r, n in exit_reasons.most_common():
        print(f"  {n:3d}  {r}")

    incomplete = sum(1 for s in summaries if s["incomplete"])
    print(f"\n=== LOOPS: {len(summaries)} total, {incomplete} incomplete ===")
    print(f"  Token totals (loop_end sums): prompt={token_total['prompt']:,} completion={token_total['completion']:,}")

    print("\n=== RECENT 20 LOOPS ===")
    for s in summaries[-20:]:
        inc = " [NO loop_end]" if s["incomplete"] else ""
        print(
            f"{s['start']} | {s['source'][:38]:38} | "
            f"it={s['iters']:2} dur={s['dur_s']:7.1f}s tc={s['tc']:3} "
            f"terr={s['tool_err']:2}{inc} | {s['exit']}"
        )

    print("\n=== TOP TOOLS (from loop_end) ===")
    by_tool = defaultdict(lambda: {"ok": 0, "err": 0})
    for (t, k), c in tool_stats.items():
        by_tool[t][k] = c
    for t in sorted(by_tool, key=lambda x: -(by_tool[x]["ok"] + by_tool[x]["err"]))[:18]:
        o, e = by_tool[t]["ok"], by_tool[t]["err"]
        print(f"  {t:22} ok={o:4} err={e:4}")

    print("\n=== TOP ERROR PREVIEWS ===")
    for msg, n in errors.most_common(12):
        print(f"  {n:3d}x  {msg}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
