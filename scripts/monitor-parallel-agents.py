#!/usr/bin/env python3
"""Snapshot parallel agent test progress from runtime.log + agent dirs."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

AGENTS = {
    "45bcde61-9b80-4b0f-8398-ed73c97ca651": "Qwen/LAN",
    "e308c622-b080-41ba-9b36-b588d00ae43c": "Gemini",
    "8314bba6-cb9b-4e3e-a639-a5b32ffdcb37": "Claude",
    "5010424f-3348-4ff2-9ce7-b55e5b8babde": "GPT",
}

ADAPTER_RE = re.compile(r"adapter=([^\s]+)")
BABO_CLOUD_RE = re.compile(r"agent=([0-9a-f-]{36}).*Babo Cloud inference relay")
LAN_VLLM_RE = re.compile(r"agent=([0-9a-f-]{36}).*POST http://192\.168\.")

LOG_PATTERNS = {
    "agentic_start": re.compile(r"agent=([0-9a-f-]{36}).*\[AGENTIC\] entering"),
    "loop_end": re.compile(r"\[LOOP:([0-9a-f]+)\] === END === exit=(\S+)"),
    "token_summary": re.compile(
        r"\[LOOP:([0-9a-f]+)\] TOKEN SUMMARY — orchestrator: prompt=(\d+) completion=(\d+) total=(\d+) \| delegates: prompt=(\d+) completion=(\d+) total=(\d+) \| combined: prompt=(\d+) completion=(\d+) total=(\d+) \| (\d+) iterations"
    ),
    "gen_iter": re.compile(r"agent=([0-9a-f-]{36}).*\[GEN\] iter=(\d+)"),
    "error_400": re.compile(r"ERROR.*400 Bad Request"),
    "error_404": re.compile(r"404"),
    "deleted": re.compile(r"Agent ([0-9a-f-]{36}) deleted"),
    "team_launch": re.compile(r"TeamManager: launched team (\S+)"),
    "babo_cloud": re.compile(r"Babo Cloud inference relay"),
    "lan_vllm": re.compile(r"POST http://192\.168\.[\d.]+:8000"),
    "nls_signal": re.compile(r"nls_signal", re.I),
}


def load_session_meta(agents_dir: Path, agent_id: str) -> dict:
    p = agents_dir / agent_id / "session_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def tail_lines(path: Path, max_bytes: int = 400_000) -> list[str]:
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()
        data = f.read().decode("utf-8", errors="replace")
    return data.splitlines()


def summarize_agent(lines: list[str], agent_id: str, label: str, meta: dict) -> dict:
    short = agent_id[:8]
    relevant = [ln for ln in lines if agent_id in ln or short in ln]
    last_20 = relevant[-20:]

    loops: list[str] = []
    errors: list[str] = []
    last_gen_iter = None
    last_token = None
    agentic = False
    for ln in relevant:
        if m := LOG_PATTERNS["loop_end"].search(ln):
            loops.append(m.group(2))
        if m := LOG_PATTERNS["token_summary"].search(ln):
            last_token = {
                "orch_total": int(m.group(4)),
                "del_total": int(m.group(7)),
                "combined_total": int(m.group(10)),
                "iters": int(m.group(11)),
            }
        if m := LOG_PATTERNS["gen_iter"].search(ln):
            if m.group(1) == agent_id:
                last_gen_iter = int(m.group(2))
        if "ERROR" in ln or "WARNING" in ln and ("400" in ln or "404" in ln or "failed" in ln.lower()):
            if agent_id in ln:
                errors.append(ln.split("|")[-1].strip()[:120])
        if LOG_PATTERNS["agentic_start"].search(ln):
            agentic = True

    model = meta.get("orchestrator_model") or "Qwen/LAN (default)"
    turn = meta.get("turn_count", "?")

    last_adapter = None
    uses_babo_cloud = False
    uses_lan = False
    project_install_errors = 0
    for ln in relevant:
        if m := ADAPTER_RE.search(ln):
            last_adapter = m.group(1)
        if BABO_CLOUD_RE.search(ln):
            uses_babo_cloud = True
        if LAN_VLLM_RE.search(ln) or ("agent=" + agent_id in ln and "192.168." in ln and "POST" in ln):
            uses_lan = True
        if "project_install" in ln and "error=True" in ln:
            project_install_errors += 1

    route = "unknown"
    orch = meta.get("orchestrator_model") or ""
    if orch and not orch.lower().startswith("qwen"):
        route = f"Babo Cloud ({orch})"
    elif uses_babo_cloud:
        route = "Babo Cloud"
    elif uses_lan or not orch:
        route = "LAN vLLM"
    if last_adapter:
        route = f"{route} · gen={last_adapter}"

    status = "idle"
    if last_gen_iter and last_gen_iter >= 1:
        status = f"agentic iter {last_gen_iter}"
    elif agentic or (last_token and last_token["iters"] > 0):
        status = "agentic/teams"
    if errors:
        status = f"{status} (+errors)"

    return {
        "label": label,
        "agent_id": agent_id,
        "model": model,
        "route": route,
        "turns": turn,
        "status": status,
        "last_loop_exit": loops[-1] if loops else None,
        "tokens": last_token,
        "recent_errors": errors[-3:],
        "project_install_errors": project_install_errors,
        "last_log": last_20[-1].split("|")[-1].strip()[:100] if last_20 else "(no recent log)",
    }


def main() -> int:
    data_root = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path.home() / "AppData/Roaming/babo-desktop"
    )
    log_path = data_root / "runtime.log"
    agents_dir = data_root / "data" / "agents"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = tail_lines(log_path)

    print(f"=== PARALLEL AGENT MONITOR @ {now} ===")
    print(f"log: {log_path} ({log_path.stat().st_size // 1024}KB)" if log_path.exists() else "log: missing")
    print()

    global_errors = []
    for ln in lines[-500:]:
        if LOG_PATTERNS["error_400"].search(ln):
            global_errors.append(ln.split("|")[-1].strip()[:140])
        if "nls_signal" in ln.lower():
            global_errors.append("nls_signal leak: " + ln.split("|")[-1].strip()[:100])

    for agent_id, label in AGENTS.items():
        meta = load_session_meta(agents_dir, agent_id)
        if not (agents_dir / agent_id).exists():
            print(f"[{label}] {agent_id[:8]} — MISSING (deleted?)")
            continue
        s = summarize_agent(lines, agent_id, label, meta)
        tok = s["tokens"]
        tok_str = (
            f"combined={tok['combined_total']:,} iters={tok['iters']}"
            if tok
            else "no loop summary yet"
        )
        print(f"[{s['label']}] {agent_id[:8]} model={s['model']}")
        print(f"  route={s['route']} turns={s['turns']} status={s['status']} {tok_str}")
        if s["project_install_errors"]:
            print(f"  project_install_errors={s['project_install_errors']}")
        if s["last_loop_exit"]:
            print(f"  last_loop_exit={s['last_loop_exit']}")
        if s["recent_errors"]:
            print(f"  errors: {s['recent_errors'][-1]}")
        print(f"  last: {s['last_log']}")
        print()

    if global_errors:
        uniq = list(dict.fromkeys(global_errors))[-5:]
        print("GLOBAL ISSUES (recent):")
        for e in uniq:
            print(f"  - {e}")
        print()

    print("AGENT_LOOP_TICK_PARALLEL_MONITOR done")

    report_path = data_root / "parallel-monitor.log"
    with report_path.open("a", encoding="utf-8") as out:
        out.write(f"\n--- {now} ---\n")
        for agent_id, label in AGENTS.items():
            if not (agents_dir / agent_id).exists():
                out.write(f"[{label}] MISSING\n")
                continue
            s = summarize_agent(lines, agent_id, label, load_session_meta(agents_dir, agent_id))
            tok = s["tokens"]
            tok_str = (
                f"combined={tok['combined_total']:,} iters={tok['iters']}"
                if tok
                else "no loop summary"
            )
            out.write(f"[{s['label']}] {s['status']} turns={s['turns']} {tok_str}\n")
            if s["recent_errors"]:
                out.write(f"  err: {s['recent_errors'][-1]}\n")
        if global_errors:
            out.write(f"global: {global_errors[-1]}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
