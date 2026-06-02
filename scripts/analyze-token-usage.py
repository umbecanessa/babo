#!/usr/bin/env python3
"""Analyze agentic token/context growth from loop jsonl logs and diag dumps."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

AGENTS = {
    "45bcde61-9b80-4b0f-8398-ed73c97ca651": "Qwen/LAN",
    "e308c622-b080-41ba-9b36-b588d00ae43c": "Gemini",
    "8314bba6-cb9b-4e3e-a639-a5b32ffdcb37": "Claude",
    "5010424f-3348-4ff2-9ce7-b55e5b8babde": "GPT",
}

DIAG_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "nls_agentic_diag"
LOG_PATH = Path(os.environ.get("APPDATA", "")) / "babo-desktop" / "runtime.log"
DATA_ROOT = Path(os.environ.get("APPDATA", "")) / "babo-desktop" / "data" / "agents"


def analyze_loop_jsonl(path: Path) -> dict:
    rows = []
    loop_id = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONError:
            continue
        loop_id = ev.get("loop_id") or loop_id
        if ev.get("event") == "generation":
            rows.append({
                "iter": ev.get("iteration"),
                "ctx_msgs": None,
                "prompt": ev.get("prompt_tokens") or 0,
                "completion": ev.get("completion_tokens") or 0,
                "total": ev.get("total_tokens") or 0,
                "cum_prompt": ev.get("cumulative_prompt_tokens") or 0,
                "error": ev.get("error"),
                "tools": ev.get("tool_calls_count") or 0,
            })
        elif ev.get("event") == "iteration_start":
            if rows and rows[-1]["iter"] == ev.get("iteration"):
                rows[-1]["ctx_msgs"] = ev.get("ctx_msgs")
            else:
                rows.append({
                    "iter": ev.get("iteration"),
                    "ctx_msgs": ev.get("ctx_msgs"),
                    "prompt": 0,
                    "completion": 0,
                    "total": 0,
                    "cum_prompt": 0,
                    "error": None,
                    "tools": 0,
                })
        elif ev.get("event") == "loop_end":
            return {
                "loop_id": loop_id,
                "path": str(path),
                "exit": ev.get("exit"),
                "iterations": ev.get("iterations"),
                "rows": rows,
                "token_summary": ev.get("token_summary"),
            }
    return {"loop_id": loop_id, "path": str(path), "exit": None, "iterations": len(rows), "rows": rows}


def estimate_diag_body_kb(diag_dir: Path, iteration: int) -> dict | None:
    p = diag_dir / f"iter_{iteration}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    msg_chars = sum(m.get("content_len", 0) for m in data.get("messages_summary", []))
    n_schemas = data.get("n_tool_schemas", 0)
    # Rough schema bytes: ~2.5KB avg per tool in executing mode (empirical)
    est_schema_chars = n_schemas * 2500
    est_json_chars = msg_chars + est_schema_chars + 8000  # overhead
    return {
        "iteration": iteration,
        "n_messages": data.get("n_messages"),
        "msg_chars": msg_chars,
        "n_tool_schemas": n_schemas,
        "est_body_kb": round(est_json_chars / 1024, 1),
    }


def scan_agent(agent_id: str, label: str) -> dict:
    agent_dir = DATA_ROOT / agent_id / "agentic_logs"
    loops = []
    if agent_dir.exists():
        for f in sorted(agent_dir.glob("loop_*.jsonl"), key=lambda x: x.stat().st_mtime):
            loops.append(analyze_loop_jsonl(f))

    # Top loops by peak prompt tokens
    scored = []
    for lp in loops:
        rows = lp.get("rows") or []
        peak = max((r.get("prompt") or 0 for r in rows), default=0)
        last_ctx = 0
        for r in rows:
            if r.get("ctx_msgs"):
                last_ctx = r["ctx_msgs"]
        scored.append({**lp, "peak_prompt": peak, "last_ctx_msgs": last_ctx})

    scored.sort(key=lambda x: x["peak_prompt"], reverse=True)

    # Diag dumps tied to this agent's workspace paths in any iter_1.json under diag
    diag_loops = []
    if DIAG_ROOT.exists():
        for d in DIAG_ROOT.iterdir():
            if not d.is_dir():
                continue
            sample = d / "iter_1.json"
            if not sample.exists():
                continue
            text = sample.read_text(encoding="utf-8", errors="replace")
            if agent_id in text:
                iters = sorted(int(x.stem.split("_")[1]) for x in d.glob("iter_*.json") if "_resp" not in x.name)
                first = estimate_diag_body_kb(d, iters[0]) if iters else None
                last = estimate_diag_body_kb(d, iters[-1]) if iters else None
                diag_loops.append({
                    "loop_id": d.name,
                    "max_iter": max(iters) if iters else 0,
                    "first": first,
                    "last": last,
                })

    return {"label": label, "agent_id": agent_id, "loop_count": len(loops), "top_loops": scored[:8], "diag_loops": diag_loops[:10]}


def scan_log_compaction(log_path: Path, agent_ids: set[str]) -> dict:
    if not log_path.exists():
        return {}
    compact = defaultdict(int)
    compose = defaultdict(list)
    token_summaries = defaultdict(list)
    gen_errors = defaultdict(int)

    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        for aid in agent_ids:
            if aid not in line:
                continue
        if "Anchored compaction:" in line or "Simple compaction:" in line or "PRE-GENERATE compaction" in line:
            for aid in agent_ids:
                if aid in line:
                    compact[aid] += 1
        m = re.search(r"agent=([0-9a-f-]{36}).*compose_context: phase=(\w+) msgs=\d+ chars=(\d+)", line)
        if m and m.group(1) in agent_ids:
            compose[m.group(1)].append(int(m.group(3)))
        m2 = re.search(r"Agent ([0-9a-f-]{36}).*GENERATION ERROR", line)
        if m2:
            gen_errors[m2.group(1)] += 1
        if "TOKEN SUMMARY" in line:
            for aid in agent_ids:
                if aid in line:
                    token_summaries[aid].append(line.split("|")[-1].strip()[:120])

    return {
        "compactions": dict(compact),
        "compose_chars_max": {k: max(v) if v else 0 for k, v in compose.items()},
        "compose_chars_avg": {k: round(sum(v) / len(v)) if v else 0 for k, v in compose.items()},
        "gen_errors": dict(gen_errors),
        "token_summaries_tail": {k: v[-3:] for k, v in token_summaries.items()},
    }


def main() -> None:
    reports = []
    for aid, label in AGENTS.items():
        reports.append(scan_agent(aid, label))

    log_stats = scan_log_compaction(LOG_PATH, set(AGENTS.keys()))

    print("=" * 72)
    print("TOKEN / CONTEXT USAGE ANALYSIS — parallel test agents")
    print("=" * 72)

    for rep in reports:
        print(f"\n## {rep['label']} ({rep['agent_id'][:8]}) — {rep['loop_count']} loops logged")
        for lp in rep["top_loops"][:5]:
            rows = lp.get("rows") or []
            if not rows:
                continue
            peak_row = max(rows, key=lambda r: r.get("prompt") or 0)
            growth = []
            prev = 0
            for r in rows:
                p = r.get("prompt") or 0
                if p > 0:
                    growth.append(p - prev if prev else p)
                    prev = p
            avg_step = round(sum(growth) / len(growth)) if growth else 0
            print(
                f"  loop {lp.get('loop_id','?')[:12]} exit={lp.get('exit')} "
                f"iters={lp.get('iterations')} peak_prompt={lp.get('peak_prompt'):,} "
                f"last_ctx_msgs={lp.get('last_ctx_msgs')} avg_prompt_step=+{avg_step:,}"
            )
            # show last 3 iters with prompt>0
            tail = [r for r in rows if (r.get("prompt") or 0) > 0][-3:]
            for t in tail:
                print(
                    f"    iter {t['iter']:>2}: prompt={t['prompt']:,} ctx_msgs={t.get('ctx_msgs')} "
                    f"completion={t.get('completion',0):,}"
                )

        if rep["diag_loops"]:
            print("  Diag dump growth (est. HTTP body incl. ~2.5KB/tool schema):")
            for d in rep["diag_loops"][:5]:
                f, l = d.get("first"), d.get("last")
                if f and l:
                    print(
                        f"    {d['loop_id'][:12]} iter {f['iteration']}→{l['iteration']}: "
                        f"msgs {f['n_messages']}->{l['n_messages']}, "
                        f"content {f['msg_chars']:,}->{l['msg_chars']:,} chars, "
                        f"est_body ~{f['est_body_kb']}->~{l['est_body_kb']} KB "
                        f"(Nest limit 100 KB)"
                    )

    print("\n## Log-level signals (runtime.log)")
    for aid, label in AGENTS.items():
        print(f"  {label}: compactions={log_stats.get('compactions', {}).get(aid, 0)} "
              f"gen_errors={log_stats.get('gen_errors', {}).get(aid, 0)} "
              f"compose_max_chars={log_stats.get('compose_chars_max', {}).get(aid, 0):,} "
              f"compose_avg_chars={log_stats.get('compose_chars_avg', {}).get(aid, 0):,}")

    print("\n## Key findings template")
    print("  - Compaction trigger ~50K tokens; Nest limit ~25-30K tokens JSON → compaction too late for cloud")
    print("  - PRD read (~12K chars) stays in history unless compacted")
    print("  - Executing mode: 38 tool schemas ≈ 90-100KB alone")


if __name__ == "__main__":
    main()
