#!/usr/bin/env python3
"""Sequential Babo agent scenario runner.

Creates one agent per scenario (or reuses --agent-id), sends prompts over the
runtime WebSocket, auto-answers ask_user(), scores pass/fail heuristics, and
writes a JSON + Markdown report. Run scenarios in parallel with --parallel N.

Requires Babo desktop (or local runtime) listening on loopback.

Examples:
  python scripts/run-agent-scenarios.py --dry-run
  python scripts/run-agent-scenarios.py --tags smoke
  python scripts/run-agent-scenarios.py --category personal_assistant
  python scripts/run-agent-scenarios.py --tags tier1
  python scripts/run-agent-scenarios.py --include-manual --only com-03
  python scripts/run-agent-scenarios.py --parallel 5 --exclude-tags smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_SCENARIOS = SCRIPT_DIR / "scenarios"
DEFAULT_DATA_ROOT = Path.home() / "AppData" / "Roaming" / "babo-desktop"


def _configure_console_encoding() -> None:
    """Avoid UnicodeEncodeError on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


@dataclass
class ScenarioResult:
    scenario_id: str
    name: str
    category: str
    agent_id: str | None = None
    status: str = "pending"  # pass | fail | error | skipped | timeout
    duration_s: float = 0.0
    final_response: str = ""
    events: dict[str, int] = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    ws_errors: list[str] = field(default_factory=list)
    pass_reasons: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    forensics: dict[str, Any] = field(default_factory=dict)
    exit_reason: str = ""
    aborted: bool = False


def load_scenarios_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    scenarios = raw.get("scenarios") or []
    return defaults, scenarios


def load_scenarios(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load one YAML file or merge all *.yaml in a directory (_defaults.yaml first)."""
    if path.is_file():
        return load_scenarios_file(path)

    defaults: dict[str, Any] = {}
    scenarios: list[dict[str, Any]] = []
    defaults_path = path / "_defaults.yaml"
    if defaults_path.exists():
        defaults, _ = load_scenarios_file(defaults_path)

    for f in sorted(path.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        file_defaults, file_scenarios = load_scenarios_file(f)
        if not defaults and file_defaults:
            defaults = file_defaults
        scenarios.extend(file_scenarios)

    return defaults, scenarios


def resolve_runtime_port(data_root: Path, override: int | None) -> int:
    if override:
        return override
    cfg_path = data_root / "nls-config.json"
    if cfg_path.exists():
        try:
            return int(json.loads(cfg_path.read_text(encoding="utf-8")).get("runtimePort", 9222))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return 9222


def filter_scenarios(
    scenarios: list[dict[str, Any]],
    *,
    category: str | None,
    tags: set[str] | None,
    exclude_tags: set[str] | None,
    only: set[str] | None,
    include_disabled: bool,
    include_slow: bool,
    include_manual: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sc in scenarios:
        if sc.get("disabled") and not include_disabled:
            continue
        if sc.get("manual") and not include_manual:
            continue
        sc_tags = set(sc.get("tags") or [])
        if exclude_tags and (exclude_tags & sc_tags):
            continue
        if not include_slow and "slow" in sc_tags:
            continue
        if only and sc.get("id") not in only:
            continue
        if category and sc.get("category") != category:
            continue
        if tags and not (tags & sc_tags):
            continue
        out.append(sc)
    return out


def merge_defaults(defaults: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **scenario}
    merged["pass"] = {**(defaults.get("pass") or {}), **(scenario.get("pass") or {})}
    return merged


def _rule_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


async def wait_for_health(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{base_url}/health", timeout=5.0)
                if r.status_code == 200:
                    body = r.json()
                    if body.get("status") in ("healthy", "loading"):
                        return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Runtime not healthy at {base_url} after {timeout_s:.0f}s")


async def create_agent(client: httpx.AsyncClient, base_url: str, name: str) -> str:
    body = {
        "name": name,
        "soulWish": "Automated scenario harness agent",
    }
    r = await client.post(f"{base_url}/agents", json=body, timeout=60.0)
    r.raise_for_status()
    return r.json()["agent_id"]


async def delete_agent(client: httpx.AsyncClient, base_url: str, agent_id: str) -> None:
    r = await client.delete(f"{base_url}/agents/{agent_id}", timeout=60.0)
    if r.status_code not in (200, 404):
        r.raise_for_status()


async def set_agent_model(
    client: httpx.AsyncClient,
    base_url: str,
    agent_id: str,
    model: str,
) -> None:
    r = await client.patch(
        f"{base_url}/agents/{agent_id}/inference",
        json={"orchestrator_model": model},
        timeout=30.0,
    )
    r.raise_for_status()


def seed_agent_workspace(
    data_root: Path,
    agent_id: str,
    seeds: list[str],
    *,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Copy repo files into an agent's isolated workspace (for codebase scenarios)."""
    workspace = data_root / "data" / "agents" / agent_id / "workspace"
    for rel in seeds:
        rel_path = Path(rel)
        src = (repo_root / rel_path).resolve()
        if not src.is_file():
            continue
        dest = workspace / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def collect_tools_from_event(msg: dict[str, Any], tools: set[str]) -> None:
    etype = msg.get("type") or ""
    for key in ("tool_calls", "tool_results"):
        for item in msg.get(key) or []:
            if isinstance(item, dict):
                name = item.get("tool") or item.get("name") or item.get("tool_name")
                if name:
                    tools.add(str(name).split("(")[0].strip())
    if etype in ("tool_progress", "tool_execution_start", "tool_execution_end", "tool_output_chunk"):
        name = msg.get("tool") or msg.get("tool_name")
        if name:
            tools.add(str(name).split("(")[0].strip())
    if etype == "agentic_iteration":
        collect_tools_from_event(
            {"tool_calls": msg.get("tool_calls"), "tool_results": msg.get("tool_results")},
            tools,
        )


_DEFAULT_FAIL_PHRASES = (
    "generation failed",
    "429 too many requests",
    "i'm having trouble generating",
    "please try again",
)

# Keep in sync with nls.agentic.types.LoopState.to_result() non-aborted exits.
_NON_ABORT_EXIT_REASONS = frozenset({
    "",
    "task_complete",
    "tool_requested_stop",
    "orchestrator_terminated",
    "awaiting_delegates",
    "idle_monitor_yield",
    "post_launch_yield",
    "coordinator_burn",
    "monitor_iter_cap",
    "idle_monitor",
    "wake_token_budget",
    "checkback_suppressed",
})

_LOOP_STOPPED_RE = re.compile(r"\[loop stopped:\s*([^\].]+)", re.IGNORECASE)


def _loop_exit_is_failure(final_response: str, exit_reason: str) -> bool:
    """True when the agentic loop aborted (not an intentional coordinator handover)."""
    reason = (exit_reason or "").strip()
    if reason in _NON_ABORT_EXIT_REASONS:
        return False
    if final_response.strip() in _NON_ABORT_EXIT_REASONS:
        return False
    match = _LOOP_STOPPED_RE.search(final_response)
    if match:
        embedded = match.group(1).split(".")[0].strip()
        return embedded not in _NON_ABORT_EXIT_REASONS
    return "loop stopped:" in final_response.lower()


def tools_from_agentic_logs(agent_dir: Path) -> set[str]:
    logs_dir = agent_dir / "agentic_logs"
    if not logs_dir.is_dir():
        return set()
    found: set[str] = set()
    for path in sorted(logs_dir.glob("loop_*.jsonl")):
        if path.name.startswith("loop_journal"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "tool_result":
                name = ev.get("tool_name") or ev.get("tool")
                if name:
                    found.add(str(name).split("(")[0].strip())
            for bucket in (ev.get("tool_successes") or {}, ev.get("tool_errors") or {}):
                if isinstance(bucket, dict):
                    found.update(str(k).split("(")[0].strip() for k in bucket if k)
            end_tools = ev.get("tool_errors") or ev.get("tool_successes")
            if ev.get("event") == "loop_end":
                for bucket in (ev.get("tool_errors") or {}, ev.get("tool_successes") or {}):
                    if isinstance(bucket, dict):
                        found.update(str(k).split("(")[0].strip() for k in bucket if k)
    return found


def _artifact_text_contains(
    agent_dir: Path | None,
    rel_paths: list[str],
    needle: str,
) -> bool:
    if agent_dir is None or not needle:
        return False
    ws = agent_dir / "workspace"
    for rel in rel_paths:
        fp = ws / rel
        if not fp.is_file():
            continue
        try:
            if needle.lower() in fp.read_text(encoding="utf-8", errors="replace").lower():
                return True
        except OSError:
            pass
    return False


def _artifact_tools_satisfied(
    agent_dir: Path | None,
    artifact_files: list[str],
    tools_any: list[str],
) -> bool:
    """True when required artifact files exist (substitute for write tool)."""
    if agent_dir is None or "write" not in tools_any:
        return False
    ws = agent_dir / "workspace"
    return any((ws / rel).is_file() for rel in artifact_files)


def _is_spurious_agentic_complete(msg: dict[str, Any], tools_used: set[str]) -> bool:
    """Ignore background scheduler preemption during harness waits."""
    if tools_used:
        return False
    exit_reason = str(msg.get("exit_reason") or msg.get("abort_reason") or "").strip()
    if not msg.get("aborted"):
        return False
    if msg.get("autonomous") and exit_reason in (
        "user_abort", "orchestration_preempted", "",
    ):
        return True
    if exit_reason != "user_abort":
        return False
    blob = " ".join(
        str(msg.get(k) or "")
        for k in ("final_response", "content", "response", "message")
    ).upper()
    return "SCHEDULER_OK" in blob or not blob.strip()


def score_pass(
    cfg: dict[str, Any],
    *,
    final_response: str,
    tools_used: set[str],
    ws_errors: list[str],
    completed: bool,
    exit_reason: str = "",
    aborted: bool = False,
    agent_dir: Path | None = None,
) -> tuple[bool, list[str], list[str]]:
    rules = cfg.get("pass") or {}
    ok_reasons: list[str] = []
    bad_reasons: list[str] = []

    if not completed:
        bad_reasons.append("did not receive agentic_complete or response_end")

    acceptable_exits = set(_rule_list(rules.get("acceptable_exit_reasons")))
    intentional_yield = (
        exit_reason in _NON_ABORT_EXIT_REASONS
        or exit_reason in acceptable_exits
        or final_response.strip() in _NON_ABORT_EXIT_REASONS
    )
    if intentional_yield and exit_reason:
        ok_reasons.append(f"coordinator handover: {exit_reason}")

    resp_lower = final_response.lower()
    for phrase in _rule_list(rules.get("response_must_not_contain")) + list(_DEFAULT_FAIL_PHRASES):
        if phrase.lower() in resp_lower:
            bad_reasons.append(f"response contains failure phrase: {phrase!r}")

    if _loop_exit_is_failure(final_response, exit_reason) or (
        aborted
        and exit_reason not in _NON_ABORT_EXIT_REASONS
        and exit_reason not in ("orchestration_preempted",)
    ):
        bad_reasons.append(
            f"agentic loop aborted (exit_reason={exit_reason or 'unknown'!r})"
        )

    min_chars = rules.get("min_response_chars")
    if min_chars is not None:
        if len(final_response.strip()) >= int(min_chars):
            ok_reasons.append(f"response length >= {min_chars}")
        elif intentional_yield:
            ok_reasons.append(
                f"short response waived — coordinator handover ({exit_reason})"
            )
        else:
            bad_reasons.append(f"response too short ({len(final_response.strip())} < {min_chars})")

    artifact_files = _rule_list(rules.get("artifact_files"))
    for needle in _rule_list(rules.get("response_contains")):
        if needle.lower() in final_response.lower():
            ok_reasons.append(f"contains '{needle}'")
        elif artifact_files and _artifact_text_contains(agent_dir, artifact_files, needle):
            ok_reasons.append(
                f"contains '{needle}' in artifact file(s): {', '.join(artifact_files)}"
            )
        else:
            bad_reasons.append(f"missing '{needle}' in response")

    any_needles = _rule_list(rules.get("response_contains_any"))
    if any_needles:
        if any(n.lower() in final_response.lower() for n in any_needles):
            ok_reasons.append("response_contains_any satisfied")
        else:
            bad_reasons.append(f"response missing all of {any_needles}")

    tools_any = rules.get("tools_any") or []
    if tools_any:
        matched = [t for t in tools_any if t in tools_used]
        if matched:
            ok_reasons.append(f"used tool(s): {', '.join(matched)}")
        elif artifact_files and _artifact_tools_satisfied(agent_dir, artifact_files, tools_any):
            ok_reasons.append(
                f"artifact file(s) present: {', '.join(artifact_files)}"
            )
        else:
            bad_reasons.append(f"expected one of tools {tools_any}, got {sorted(tools_used) or 'none'}")

    tools_not_any = rules.get("tools_not_any") or []
    if tools_not_any:
        forbidden = [t for t in tools_not_any if t in tools_used]
        if forbidden:
            bad_reasons.append(f"forbidden tool(s) used: {', '.join(forbidden)}")
        else:
            ok_reasons.append(f"avoided tools: {', '.join(tools_not_any)}")

    if rules.get("no_ws_errors", True) and ws_errors:
        bad_reasons.append(f"{len(ws_errors)} websocket error(s)")

    if bad_reasons:
        return False, ok_reasons, bad_reasons
    if not ok_reasons and completed:
        ok_reasons.append("completed without explicit rules")
    return bool(completed and not bad_reasons), ok_reasons, bad_reasons


def summarize_agentic_logs(agent_dir: Path) -> dict[str, Any]:
    logs_dir = agent_dir / "agentic_logs"
    if not logs_dir.is_dir():
        return {"loops": 0}
    loops = sorted(logs_dir.glob("loop_*.jsonl"))
    exit_reasons: dict[str, int] = {}
    tool_ok = 0
    tool_err = 0
    for path in loops[-5:]:
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        end = next((e for e in reversed(events) if e.get("event") == "loop_end"), None)
        if end:
            exit_reasons[end.get("exit_reason", "?")] = exit_reasons.get(end.get("exit_reason", "?"), 0) + 1
        for e in events:
            if e.get("event") == "tool_result":
                if e.get("success"):
                    tool_ok += 1
                else:
                    tool_err += 1
    return {
        "loops": len(loops),
        "recent_exit_reasons": exit_reasons,
        "tool_ok": tool_ok,
        "tool_err": tool_err,
    }


async def run_chat_turn(
    ws_url: str,
    content: str,
    *,
    timeout_s: float,
    auto_answer: str,
    model: str | None,
) -> tuple[bool, str, dict[str, int], set[str], list[str], str, bool]:
    """Send one user message; wait until turn completes."""
    events: dict[str, int] = {}
    tools_used: set[str] = set()
    ws_errors: list[str] = []
    token_chunks: list[str] = []
    communicate_parts: list[str] = []
    explicit_final = ""
    exit_reason = ""
    aborted = False
    completed = False
    agentic = False

    payload: dict[str, Any] = {"type": "message", "content": content}
    if model:
        payload["model"] = model

    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps(payload))
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = msg.get("type") or "?"
            events[etype] = events.get(etype, 0) + 1
            collect_tools_from_event(msg, tools_used)

            if etype == "error":
                ws_errors.append(str(msg.get("content") or msg.get("message") or msg))

            if etype in ("token", "agentic_token"):
                token_chunks.append(msg.get("content") or "")

            if etype == "communicate":
                part = (msg.get("message") or msg.get("content") or "").strip()
                if part:
                    communicate_parts.append(part)

            if etype == "response_replace":
                repl = (msg.get("response") or msg.get("content") or "").strip()
                if repl:
                    token_chunks = [repl]

            if etype == "agentic_start":
                agentic = True

            if etype == "agentic_complete":
                if _is_spurious_agentic_complete(msg, tools_used):
                    agentic = False
                    continue
                completed = True
                exit_reason = str(
                    msg.get("exit_reason") or msg.get("abort_reason") or ""
                ).strip()
                aborted = bool(msg.get("aborted"))
                explicit_final = (
                    msg.get("final_response")
                    or msg.get("abort_reason")
                    or msg.get("exit_reason")
                    or ""
                ).strip()
                break

            if etype == "response_end" and not agentic:
                completed = True
                explicit_final = (msg.get("response") or "").strip()
                break

            if etype == "response_end" and agentic:
                fr = (msg.get("response") or "").strip()
                if fr:
                    explicit_final = fr

            if etype == "ask_user":
                answer = auto_answer
                await ws.send(json.dumps({"type": "user_answer", "content": answer}))
                events["auto_user_answer"] = events.get("auto_user_answer", 0) + 1

    final_response = explicit_final
    if not final_response and communicate_parts:
        final_response = "\n\n".join(communicate_parts)
    if not final_response and token_chunks:
        final_response = "".join(token_chunks).strip()
    return completed, final_response, events, tools_used, ws_errors, exit_reason, aborted


async def setup_agent_name(
    ws_url: str,
    setup_name: str,
    *,
    birth_timeout_s: float,
    model: str | None,
) -> None:
    """Consume birth greeting, then assign agent name."""
    events: dict[str, int] = {}
    greeted = False
    deadline = time.monotonic() + birth_timeout_s

    async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            etype = msg.get("type") or "?"
            events[etype] = events.get(etype, 0) + 1
            if etype in ("response_end", "agentic_complete"):
                greeted = True
                break

        if greeted:
            name_payload: dict[str, Any] = {
                "type": "message",
                "content": f"Your name is {setup_name}",
            }
            if model:
                name_payload["model"] = model
            await ws.send(json.dumps(name_payload))
            name_deadline = time.monotonic() + 120.0
            while time.monotonic() < name_deadline:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, name_deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") in ("response_end", "agentic_complete"):
                    break


async def run_scenario(
    cfg: dict[str, Any],
    *,
    base_url: str,
    ws_base: str,
    data_root: Path,
    model: str | None,
    keep_agents: bool,
    reuse_agent_id: str | None,
    verbose: bool,
    log_prefix: str = "",
    print_lock: asyncio.Lock | None = None,
) -> ScenarioResult:
    async def _log(msg: str) -> None:
        line = f"{log_prefix}{msg}"
        if print_lock is not None:
            async with print_lock:
                print(line, flush=True)
        else:
            print(line, flush=True)

    sc_id = str(cfg["id"])
    result = ScenarioResult(
        scenario_id=sc_id,
        name=str(cfg.get("name") or sc_id),
        category=str(cfg.get("category") or "unknown"),
    )
    t0 = time.monotonic()
    agent_id = reuse_agent_id
    created = False

    timeout_s = float(cfg.get("timeout_s") or 600)
    auto_answer = str(cfg.get("auto_answer") or "")
    setup_name = cfg.get("setup_name", None)
    harness_follow_up = str(cfg.get("harness_follow_up") or "").strip()
    delete_after = bool(cfg.get("delete_agent", True)) and not keep_agents and not reuse_agent_id

    async with httpx.AsyncClient() as client:
        try:
            if not agent_id:
                agent_id = await create_agent(client, base_url, f"scenario-{sc_id}")
                created = True
                if verbose:
                    await _log(f"  created agent {agent_id}")

            if model:
                await set_agent_model(client, base_url, agent_id, model)

            seeds = _rule_list(cfg.get("workspace_seed"))
            if seeds:
                seed_agent_workspace(data_root, agent_id, seeds)
                if verbose:
                    await _log(f"  seeded workspace ({len(seeds)} path(s))")

            ws_url = f"{ws_base}/ws/chat/{agent_id}"

            if not reuse_agent_id and setup_name is not None:
                await setup_agent_name(
                    ws_url,
                    str(setup_name),
                    birth_timeout_s=90.0,
                    model=model,
                )

            completed, final_response, events, tools_used, ws_errors, exit_reason, aborted = await run_chat_turn(
                ws_url,
                str(cfg["prompt"]),
                timeout_s=timeout_s,
                auto_answer=auto_answer,
                model=model,
            )

            tools_required = list((cfg.get("pass") or {}).get("tools_any") or [])
            if (
                harness_follow_up
                and completed
                and tools_required
                and not (tools_used & set(tools_required))
                and events.get("agentic_start", 0) == 0
            ):
                if verbose:
                    await _log("  harness follow-up (chat-only, no tools yet)")
                _fu_completed, _fu_response, _fu_events, _fu_tools, _fu_errors, _fu_exit, _fu_aborted = (
                    await run_chat_turn(
                        ws_url,
                        harness_follow_up,
                        timeout_s=timeout_s,
                        auto_answer=auto_answer,
                        model=model,
                    )
                )
                for k, v in _fu_events.items():
                    events[k] = events.get(k, 0) + v
                tools_used |= _fu_tools
                ws_errors.extend(_fu_errors)
                if _fu_completed:
                    completed = True
                    final_response = _fu_response or final_response
                    exit_reason = _fu_exit or exit_reason
                    aborted = _fu_aborted

            result.agent_id = agent_id
            result.events = events
            result.exit_reason = exit_reason
            result.aborted = aborted
            agent_dir = data_root / "data" / "agents" / agent_id
            if agent_dir.is_dir():
                tools_used |= tools_from_agentic_logs(agent_dir)
            result.tools_used = sorted(tools_used)
            result.ws_errors = ws_errors
            result.final_response = final_response[:4000]

            passed, ok_reasons, bad_reasons = score_pass(
                cfg,
                final_response=final_response,
                tools_used=tools_used,
                ws_errors=ws_errors,
                completed=completed,
                exit_reason=exit_reason,
                aborted=aborted,
                agent_dir=agent_dir if agent_dir.is_dir() else None,
            )

            if not completed:
                result.status = "timeout"
                result.fail_reasons = bad_reasons or ["timed out"]
            elif passed:
                result.status = "pass"
                result.pass_reasons = ok_reasons
            else:
                result.status = "fail"
                result.fail_reasons = bad_reasons
                result.pass_reasons = ok_reasons

            agent_dir = data_root / "data" / "agents" / agent_id
            if agent_dir.is_dir():
                result.forensics = summarize_agentic_logs(agent_dir)

            if delete_after and created:
                await delete_agent(client, base_url, agent_id)
                if verbose:
                    await _log(f"  deleted agent {agent_id}")

        except Exception as exc:
            result.status = "error"
            result.fail_reasons = [str(exc)]
            if created and agent_id and delete_after:
                try:
                    await delete_agent(client, base_url, agent_id)
                except Exception:
                    pass

    result.duration_s = round(time.monotonic() - t0, 1)
    return result


def print_scenario_line(
    i: int,
    total: int,
    result: ScenarioResult,
    *,
    prefix: str = "",
) -> None:
    icon = {"pass": "OK", "fail": "FAIL", "timeout": "TIMEOUT", "error": "ERR", "skipped": "SKIP"}.get(
        result.status, result.status,
    )
    tag = f"[{prefix}] " if prefix else ""
    print(f"{tag}[{i}/{total}] {icon} {result.scenario_id} ({result.duration_s}s)", flush=True)
    if result.pass_reasons:
        print(f"       + {'; '.join(result.pass_reasons)}", flush=True)
    if result.fail_reasons:
        print(f"       - {'; '.join(result.fail_reasons)}", flush=True)
    if result.tools_used:
        print(f"       tools: {', '.join(result.tools_used)}", flush=True)
    if result.final_response:
        preview = re.sub(r"\s+", " ", result.final_response)[:160]
        print(f"       -> {preview}", flush=True)


def write_report(
    out_dir: Path,
    *,
    results: list[ScenarioResult],
    meta: dict[str, Any],
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"run-{stamp}.json"
    md_path = out_dir / f"run-{stamp}.md"

    payload = {
        "meta": meta,
        "results": [r.__dict__ for r in results],
        "summary": {
            "total": len(results),
            "pass": sum(1 for r in results if r.status == "pass"),
            "fail": sum(1 for r in results if r.status == "fail"),
            "timeout": sum(1 for r in results if r.status == "timeout"),
            "error": sum(1 for r in results if r.status == "error"),
        },
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        f"# Babo scenario run — {stamp}",
        "",
        f"- Runtime: `{meta.get('base_url')}`",
        f"- Model override: `{meta.get('model') or '(agent default)'}`",
        f"- Parallelism: **{meta.get('parallel', 1)}**",
        f"- Scenarios: {payload['summary']['total']}",
        f"- Pass: **{payload['summary']['pass']}** | Fail: **{payload['summary']['fail']}** | "
        f"Timeout: **{payload['summary']['timeout']}** | Error: **{payload['summary']['error']}**",
        "",
        "| Status | ID | Category | Duration | Tools |",
        "|--------|-----|----------|----------|-------|",
    ]
    for r in results:
        tools = ", ".join(r.tools_used) if r.tools_used else "—"
        lines.append(
            f"| {r.status} | {r.scenario_id} | {r.category} | {r.duration_s}s | {tools} |",
        )
    lines.append("")
    for r in results:
        lines.extend([
            f"## {r.scenario_id} — {r.name}",
            f"**Status:** {r.status} ({r.duration_s}s)",
            "",
        ])
        if r.pass_reasons:
            lines.append(f"- Pass: {'; '.join(r.pass_reasons)}")
        if r.fail_reasons:
            lines.append(f"- Fail: {'; '.join(r.fail_reasons)}")
        if r.final_response:
            lines.append("")
            lines.append("```")
            lines.append(r.final_response[:2000])
            lines.append("```")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


async def run_scenarios_parallel(
    selected: list[dict[str, Any]],
    defaults: dict[str, Any],
    *,
    parallel: int,
    base_url: str,
    ws_base: str,
    data_root: Path,
    run_model: str | None,
    keep_agents: bool,
    reuse_agent_id: str | None,
    verbose: bool,
) -> list[ScenarioResult]:
    total = len(selected)
    sem = asyncio.Semaphore(max(1, parallel))
    print_lock = asyncio.Lock()
    results_by_idx: dict[int, ScenarioResult] = {}

    async def worker(index: int, sc: dict[str, Any]) -> None:
        cfg = merge_defaults(defaults, sc)
        sc_id = str(sc["id"])
        prefix = f"{sc_id}: "

        async with sem:
            async with print_lock:
                print(f">> {sc_id} - {cfg.get('name', sc_id)}", flush=True)
                if verbose:
                    print(
                        f"  [{sc_id}] category={cfg.get('category')} "
                        f"timeout={cfg.get('timeout_s')}s",
                        flush=True,
                    )

            try:
                result = await run_scenario(
                    cfg,
                    base_url=base_url,
                    ws_base=ws_base,
                    data_root=data_root,
                    model=run_model,
                    keep_agents=keep_agents,
                    reuse_agent_id=reuse_agent_id,
                    verbose=verbose,
                    log_prefix=prefix,
                    print_lock=print_lock,
                )
            except Exception as exc:
                result = ScenarioResult(
                    scenario_id=sc_id,
                    name=str(cfg.get("name") or sc_id),
                    category=str(cfg.get("category") or "unknown"),
                    status="error",
                    fail_reasons=[str(exc)],
                )

            results_by_idx[index] = result
            async with print_lock:
                print_scenario_line(index + 1, total, result, prefix=sc_id)
                print(flush=True)

    await asyncio.gather(*(worker(i, sc) for i, sc in enumerate(selected)))

    return [results_by_idx[i] for i in range(total)]


async def async_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run Babo agent scenarios (sequential or parallel)")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--port", type=int, default=None, help="Runtime HTTP port (default: nls-config.json)")
    parser.add_argument("--model", default=None, help="Orchestrator model override for all scenarios")
    parser.add_argument("--category", default=None, help="Filter by category")
    parser.add_argument("--tags", default=None, help="Comma-separated tags (any match)")
    parser.add_argument("--exclude-tags", default=None, help="Comma-separated tags to skip")
    parser.add_argument("--only", default=None, help="Comma-separated scenario ids")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--include-slow", action="store_true")
    parser.add_argument(
        "--include-manual",
        action="store_true",
        help="Include manual QA scenarios (channels, OAuth, desktop UI, etc.)",
    )
    parser.add_argument("--keep-agents", action="store_true", help="Do not delete agents after each scenario")
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        metavar="N",
        help="Max concurrent scenarios (default: 5). Use 1 for sequential.",
    )
    parser.add_argument("--agent-id", default=None, help="Reuse existing agent (single --only scenario)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.scenarios.exists():
        print(f"Scenarios path not found: {args.scenarios}", file=sys.stderr)
        return 2

    defaults, all_scenarios = load_scenarios(args.scenarios)
    if not all_scenarios:
        print(f"No scenarios loaded from {args.scenarios}", file=sys.stderr)
        return 2
    tag_set = set(t.strip() for t in args.tags.split(",")) if args.tags else None
    exclude_tag_set = (
        set(t.strip() for t in args.exclude_tags.split(",")) if args.exclude_tags else None
    )
    only_set = set(t.strip() for t in args.only.split(",")) if args.only else None
    run_model = args.model or defaults.get("model")

    selected = filter_scenarios(
        all_scenarios,
        category=args.category,
        tags=tag_set,
        exclude_tags=exclude_tag_set,
        only=only_set,
        include_disabled=args.include_disabled,
        include_slow=args.include_slow,
        include_manual=args.include_manual,
    )

    manual_skipped = sum(1 for sc in all_scenarios if sc.get("manual"))
    disabled_skipped = sum(1 for sc in all_scenarios if sc.get("disabled"))

    if not selected:
        print("No scenarios matched filters.", file=sys.stderr)
        if manual_skipped and not args.include_manual:
            print(f"  ({manual_skipped} manual QA scenarios skipped — use --include-manual)", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Loaded {len(all_scenarios)} scenario(s) from {args.scenarios}")
        print(f"Would run {len(selected)} after filters")
        if manual_skipped and not args.include_manual:
            print(f"  ({manual_skipped} manual QA skipped — use --include-manual)")
        if disabled_skipped and not args.include_disabled:
            print(f"  ({disabled_skipped} disabled/slow skipped — use --include-disabled --include-slow)")
        print()
        for sc in selected:
            tags = ", ".join(sc.get("tags") or [])
            flags = []
            if sc.get("manual"):
                flags.append("manual")
            if sc.get("disabled"):
                flags.append("disabled")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            print(f"  {sc['id']:24} [{sc.get('category', '?')}] tags={tags}{flag_str}")
        return 0

    port = resolve_runtime_port(args.data_root, args.port)
    base_url = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"

    print(f"Babo scenario harness - {len(selected)} scenario(s)", flush=True)
    print(f"Runtime: {base_url}", flush=True)
    if run_model:
        print(f"Model: {run_model}", flush=True)
    print(f"Parallelism: {max(1, args.parallel)}", flush=True)
    print(flush=True)

    if args.agent_id and len(selected) != 1:
        print("ERROR: --agent-id only works with a single scenario (--only)", file=sys.stderr)
        return 2
    if args.agent_id and args.parallel > 1:
        print("ERROR: --agent-id requires --parallel 1", file=sys.stderr)
        return 2

    try:
        await wait_for_health(base_url)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Start Babo desktop and wait for the runtime to be ready.", file=sys.stderr)
        return 2

    reuse = args.agent_id if args.agent_id and len(selected) == 1 else None
    parallel = max(1, args.parallel)

    if parallel == 1:
        results: list[ScenarioResult] = []
        total = len(selected)
        for i, sc in enumerate(selected, start=1):
            cfg = merge_defaults(defaults, sc)
            print(f">> {sc['id']} - {cfg.get('name', sc['id'])}", flush=True)
            if args.verbose:
                print(f"  category={cfg.get('category')} timeout={cfg.get('timeout_s')}s", flush=True)
            result = await run_scenario(
                cfg,
                base_url=base_url,
                ws_base=ws_base,
                data_root=args.data_root,
                model=run_model,
                keep_agents=args.keep_agents,
                reuse_agent_id=reuse,
                verbose=args.verbose,
            )
            results.append(result)
            print_scenario_line(i, total, result)
            print(flush=True)
    else:
        results = await run_scenarios_parallel(
            selected,
            defaults,
            parallel=parallel,
            base_url=base_url,
            ws_base=ws_base,
            data_root=args.data_root,
            run_model=run_model,
            keep_agents=args.keep_agents,
            reuse_agent_id=reuse,
            verbose=args.verbose,
        )

    json_path, md_path = write_report(
        args.data_root / "scenario-runs",
        results=results,
        meta={
            "base_url": base_url,
            "model": run_model,
            "parallel": parallel,
            "scenarios_file": str(args.scenarios),
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    passed = sum(1 for r in results if r.status == "pass")
    failed = len(results) - passed
    print(f"Done: {passed}/{len(results)} passed", flush=True)
    print(f"Report: {json_path}", flush=True)
    print(f"        {md_path}", flush=True)

    return 0 if failed == 0 else 1


def main() -> None:
    _configure_console_encoding()
    try:
        raise SystemExit(asyncio.run(async_main(sys.argv[1:])))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
