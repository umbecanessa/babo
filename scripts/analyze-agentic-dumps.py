#!/usr/bin/env python3
"""Summarize agentic loop logs + delegate dumps for parallel test agents."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

AGENTS = {
    "Qwen/LAN": "45bcde61-9b80-4b0f-8398-ed73c97ca651",
    "Gemini": "e308c622-b080-41ba-9b36-b588d00ae43c",
    "Claude": "8314bba6-cb9b-4e3e-a639-a5b32ffdcb37",
    "GPT": "5010424f-3348-4ff2-9ce7-b55e5b8babde",
}

BASE = Path.home() / "AppData/Roaming/babo-desktop/data/agents"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def analyze_agent(label: str, agent_id: str) -> dict:
    log_dir = BASE / agent_id / "agentic_logs"
    loops = sorted(log_dir.glob("loop_*.jsonl"), key=lambda p: p.stat().st_mtime)

    orch_loops = []
    delegate_loops = []
    all_tools_ok = Counter()
    all_tools_err = Counter()
    gen_errors = Counter()
    exit_reasons = Counter()
    modes = Counter()

    for lp in loops:
        events = load_jsonl(lp)
        if not events:
            continue
        start = next((e for e in events if e.get("event") == "loop_start"), None)
        end = next((e for e in reversed(events) if e.get("event") == "loop_end"), None)
        if not start:
            continue

        is_delegate = start.get("enable_delegation") is False and (
            "DELEGATE" in (start.get("user_input_preview") or "")
            or start.get("dispatch_source", "").startswith("delegate")
            or "CONTINUATION" in (start.get("user_input_preview") or "")
            or lp.name  # heuristic below
        )
        # Better heuristic: orchestrator loops usually have enable_delegation true OR team/coordinator
        goals = start.get("goals") or []
        preview = start.get("user_input_preview") or ""
        if start.get("enable_delegation") is True:
            is_delegate = False
        elif "ENGINEERING MANAGER" in preview or "PLAN RECOVERY" in preview or "team_wave" in preview.lower():
            is_delegate = False
        elif start.get("enable_delegation") is False and (
            "member" in preview.lower() or "wave" in preview.lower() or len(goals) <= 3
        ):
            is_delegate = True

        tool_ok = Counter()
        tool_err = Counter()
        for e in events:
            if e.get("event") == "tool_result":
                t = e.get("tool") or "?"
                if e.get("success"):
                    tool_ok[t] += 1
                    all_tools_ok[t] += 1
                else:
                    tool_err[t] += 1
                    all_tools_err[t] += 1
            elif e.get("event") == "generation" and e.get("error"):
                gen_errors[str(e.get("error"))[:80]] += 1
            elif e.get("event") == "mode_change":
                modes[e.get("mode") or "?"] += 1

        rec = {
            "file": lp.name,
            "loop_id": start.get("loop_id"),
            "is_delegate": is_delegate,
            "iterations": end.get("iterations") if end else None,
            "exit": end.get("exit_reason") if end else None,
            "tool_calls": end.get("total_tool_calls") if end else None,
            "goals": goals[:3],
            "preview": preview[:120],
            "tool_ok": dict(tool_ok),
            "tool_err": dict(tool_err),
            "gen_error_iters": sum(1 for e in events if e.get("event") == "generation" and e.get("error")),
        }
        if end and end.get("exit_reason"):
            exit_reasons[end.get("exit_reason")] += 1
        (delegate_loops if is_delegate else orch_loops).append(rec)

    delegates = load_jsonl(log_dir / "delegates.jsonl")
    delegate_summaries = []
    for d in delegates:
        prev = d.get("summary_preview") or ""
        stop = ""
        m = re.search(r"\[Loop stopped: ([^\]]+)\]", prev)
        if m:
            stop = m.group(1)
        elif "Generation failed" in prev:
            stop = re.search(r"Generation failed[^]]*", prev)
            stop = stop.group(0) if stop else "generation_error"
        elif prev.startswith("Initialized") or prev.startswith("Backend") or "complete" in prev.lower():
            stop = "completed_ok"
        delegate_summaries.append({
            "n": d.get("delegate_number"),
            "stop": stop[:80],
            "preview": prev[:200].replace("\n", " "),
        })

    return {
        "label": label,
        "loop_count": len(loops),
        "orch_loops": orch_loops,
        "delegate_loops": delegate_loops,
        "delegate_summaries": delegate_summaries,
        "all_tools_ok": all_tools_ok,
        "all_tools_err": all_tools_err,
        "exit_reasons": exit_reasons,
        "gen_errors": gen_errors,
    }


def main() -> None:
    for label, aid in AGENTS.items():
        r = analyze_agent(label, aid)
        print("=" * 72)
        print(f"{r['label']}  ({aid[:8]})  —  {r['loop_count']} loop logs")
        print("-" * 72)

        print("ORCHESTRATOR LOOPS (sample):")
        for lp in r["orch_loops"][:8]:
            err_tools = ", ".join(f"{k}:{v}" for k, v in lp["tool_err"].items()) or "-"
            print(
                f"  {lp['loop_id'] or '?':12} iters={lp['iterations']} exit={lp['exit']} "
                f"tc={lp['tool_calls']} err_tools=[{err_tools}]"
            )
        if len(r["orch_loops"]) > 8:
            print(f"  ... +{len(r['orch_loops']) - 8} more orchestrator loops")

        print("\nDELEGATE LOOPS (sample):")
        for lp in r["delegate_loops"][:6]:
            err_tools = ", ".join(f"{k}:{v}" for k, v in lp["tool_err"].items()) or "-"
            print(
                f"  {lp['loop_id'] or '?':12} iters={lp['iterations']} exit={lp['exit']} "
                f"err_tools=[{err_tools}]"
            )
        if len(r["delegate_loops"]) > 6:
            print(f"  ... +{len(r['delegate_loops']) - 6} more delegate loops")

        print("\nDETACHED DELEGATE SUMMARIES:")
        for d in r["delegate_summaries"][:12]:
            print(f"  #{d['n']} stop={d['stop']}")
            print(f"      {d['preview'][:160]}")

        print("\nEXIT REASONS (all loops):")
        for k, v in r["exit_reasons"].most_common():
            print(f"  {k}: {v}")

        print("\nTOOL ERRORS (aggregate):")
        for k, v in r["all_tools_err"].most_common(15):
            ok = r["all_tools_ok"].get(k, 0)
            print(f"  {k}: {v} fails / {ok} ok")

        print("\nTOP TOOLS OK:")
        for k, v in r["all_tools_ok"].most_common(12):
            print(f"  {k}: {v}")

        if r["gen_errors"]:
            print("\nGENERATION ERRORS:")
            for k, v in r["gen_errors"].most_common():
                print(f"  {v}x {k}")
        print()


if __name__ == "__main__":
    main()
