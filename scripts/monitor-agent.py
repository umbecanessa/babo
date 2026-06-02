#!/usr/bin/env python3
"""Watch runtime.log for a single agent and emit monitor ticks on key events."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

AGENT_ID = sys.argv[1] if len(sys.argv) > 1 else ""
SHORT = AGENT_ID.split("-")[0] if AGENT_ID else ""
DATA_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "AppData/Roaming/babo-desktop"
LOG = DATA_ROOT / "runtime.log"
AGENTS = DATA_ROOT / "data" / "agents"

PATTERNS = {
    "loop_end": re.compile(rf"\[LOOP:([0-9a-f]+)\] === END === exit=(\S+)"),
    "token_summary": re.compile(
        r"\[LOOP:([0-9a-f]+)\] TOKEN SUMMARY.*?combined: prompt=(\d+) completion=(\d+) total=(\d+).*?(\d+) iterations"
    ),
    "team_launch": re.compile(r"TeamManager: launched team (\S+)"),
    "team_advance": re.compile(r"team\(advance\)|action.?=.?advance"),
    "wave_wake": re.compile(r"WAVE COMPLETE|PLAN RECOVERY|PLAN CLOSURE"),
    "plan_tool": re.compile(r"tool=(plan)\("),
    "task_complete": re.compile(r"tool=task_complete"),
    "error": re.compile(r"ERROR|tool_execution_end.*error=True"),
}


def load_meta(agent_id: str) -> dict:
    p = AGENTS / agent_id / "session_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def tail_snapshot(lines: list[str], agent_id: str) -> dict:
    short = agent_id[:8]
    rel = [ln for ln in lines if agent_id in ln or short in ln]
    loops: list[str] = []
    tokens = None
    teams: list[str] = []
    recent_tools: list[str] = []
    errors: list[str] = []
    for ln in rel:
        if m := PATTERNS["loop_end"].search(ln):
            loops.append(m.group(2))
        if m := PATTERNS["token_summary"].search(ln):
            tokens = {"combined": int(m.group(4)), "iters": int(m.group(5))}
        if "TeamManager: launched team" in ln:
            teams.append(ln.split("launched team ")[-1].strip()[:36])
        if "tool_execution_end" in ln and agent_id in ln:
            m = re.search(r"tool=(\w+) error=(True|False)", ln)
            if m:
                recent_tools.append(f"{m.group(1)}:{m.group(2)}")
        if "ERROR" in ln and agent_id in ln:
            errors.append(ln.split("|")[-1].strip()[:100])
    return {
        "loops": loops[-3:],
        "tokens": tokens,
        "teams": teams[-3:],
        "tools": recent_tools[-6:],
        "errors": errors[-2:],
        "last": rel[-1].split("|")[-1].strip()[:120] if rel else "(no log yet)",
    }


def format_report(agent_id: str, snap: dict, meta: dict) -> str:
    tok = snap["tokens"]
    tok_s = f"combined={tok['combined']:,} iters={tok['iters']}" if tok else "no token summary"
    lines = [
        f"=== {agent_id[:8]} @ {datetime.now():%H:%M:%S} ===",
        f"model={meta.get('orchestrator_model', '?')} turns={meta.get('turn_count', '?')}",
        f"loops={snap['loops'] or ['—']} {tok_s}",
        f"teams={snap['teams'] or ['—']}",
        f"tools={snap['tools'] or ['—']}",
    ]
    if snap["errors"]:
        lines.append(f"errors={snap['errors']}")
    lines.append(f"last: {snap['last']}")
    return "\n".join(lines)


def is_interesting(line: str, agent_id: str) -> bool:
    if agent_id not in line and agent_id[:8] not in line:
        return False
    keys = (
        "=== END ===",
        "TOKEN SUMMARY",
        "launched team",
        "WAVE COMPLETE",
        "PLAN RECOVERY",
        "PLAN CLOSURE",
        "tool=plan(",
        "tool=task_complete",
        "Cannot advance",
        "error=True",
        "ERROR",
    )
    return any(k in line for k in keys)


def main() -> int:
    if not AGENT_ID:
        print("Usage: monitor-agent.py <agent-uuid-or-prefix> [data-root]", file=sys.stderr)
        return 1

    agent_id = AGENT_ID
    if len(agent_id) < 36:
        for d in (AGENTS).iterdir() if AGENTS.exists() else []:
            if d.name.startswith(agent_id):
                agent_id = d.name
                break

    meta = load_meta(agent_id)
    size = LOG.stat().st_size if LOG.exists() else 0
    with LOG.open("rb") as f:
        f.seek(max(0, size - 400_000))
        if size > 400_000:
            f.readline()
        lines = f.read().decode("utf-8", errors="replace").splitlines()

    snap = tail_snapshot(lines, agent_id)
    report = format_report(agent_id, snap, meta)
    print(report)
    print(f'AGENT_LOOP_TICK_aad50f8b {{"prompt":"Monitor agent {agent_id[:8]} — report status and flag regressions"}}')

    if not LOG.exists():
        return 0

    pos = LOG.stat().st_size
    while True:
        time.sleep(8)
        if not LOG.exists():
            continue
        with LOG.open("rb") as f:
            f.seek(pos)
            chunk = f.read().decode("utf-8", errors="replace")
            pos = f.tell()
        if not chunk:
            continue
        for line in chunk.splitlines():
            if is_interesting(line, agent_id):
                snap = tail_snapshot(tail_lines(LOG), agent_id)
                report = format_report(agent_id, snap, meta)
                print("\n--- EVENT ---")
                print(line.split("|")[-1].strip()[:140])
                print(report)
                print(
                    f'AGENT_LOOP_WAKE_aad50f8b {{"prompt":"Agent {agent_id[:8]} event — summarize progress, wave state, plan closure readiness, and any regressions vs prior fixes"}}'
                )
                meta = load_meta(agent_id)


def tail_lines(path: Path, max_bytes: int = 400_000) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - max_bytes))
        if size > max_bytes:
            f.readline()
        return f.read().decode("utf-8", errors="replace").splitlines()


if __name__ == "__main__":
    raise SystemExit(main())
