#!/usr/bin/env python3
"""Live test: DSH superagent drives the Kanban loop via MCP, dispatching MausBot.

Usage: /tmp/zstdvenv/bin/python scripts/live_kanban_loop_test.py
Logs to stdout and to .state/live-kanban-loop-test.jsonl (no secrets).
"""
from __future__ import annotations

import json
import re
import time
import uuid
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import zstandard

DSH = "http://127.0.0.1:3082"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
PROVIDER = "openrouter"
CWD = "/workspace/ai-bot-system"
LOG_GLOB = "/workspace/dsh-superagent-prototype/.artifacts/dsh-superagent-superagent-*.log"
SESSION_ROOT = Path("/home/box/.dsh/sessions/--workspace-ai-bot-system--")
TICKETS = Path("/workspace/ai-bot-system/tickets")
WORKTREE = Path("/workspace/aibots/workspaces/bot-001")
STATE_LOG = Path("/workspace/ai-bot-system/.state/live-kanban-loop-test.jsonl")
SUMMARY = Path("/workspace/ai-bot-system/.state/live-kanban-loop-test-summary.json")

PROMPT = """You are the Kanban manager for this box. Drive the work yourself with your MCP tools. Do not only describe the plan — call the tools.

First, call mcp__openmausbot__list_bots once to confirm OpenMausBot is reachable. Remember the bot names for your final summary.

Then run the full ticket loop for this work item. Use the kanban MCP tools (names use underscores, not dots):

Title: Add a shout option to greet
Objective: In the demo project, add a shout option to greet() in src/demo/greet.py (paths are relative to the bot worktree). When shout is true, greet should return the normal greeting in uppercase, e.g. greet("Ada", shout=True) returns "HELLO, ADA!". Default shout to false so existing greet("Ada") still returns "Hello, Ada!". Keep the existing farewell() function. Add or update tests in tests/test_greet.py (unittest).
Context: Code is in src/demo/greet.py. Tests are in tests/test_greet.py (unittest). The assigned worker is bot-001 (MausBot).
Acceptance criteria:
- greet("Ada") returns "Hello, Ada!"
- greet("Ada", shout=True) returns "HELLO, ADA!"
- existing farewell tests still pass
- all tests pass
Priority: medium. Labels: python, backend.

Required steps (do not skip):
1. mcp__kanban-tickets__ticket_create with the title, objective, context, acceptance criteria, priority, and labels above.
2. mcp__kanban-tickets__ticket_triage with apply=true. This should assign bot-001.
3. mcp__kanban-tickets__ticket_move to in-progress if it is not already there.
4. mcp__kanban-bots__bot_start_session for bot-001 and this ticket.
5. mcp__kanban-bots__bot_send_prompt with a clear implementation prompt for MausBot. Wait for that turn to finish.
6. mcp__kanban-bots__bot_get_last_turn_diff.
7. mcp__kanban-bots__bot_run_tests.
8. If tests fail or the change is incomplete, send another prompt and repeat until tests pass, with a small number of retries.
9. mcp__kanban-bots__bot_commit_turn if the change is not already committed.
10. mcp__kanban-tickets__ticket_add_review_note describing the review.
11. mcp__kanban-tickets__ticket_move to in-review, then to done.

Keep going until the ticket is in done, or you are blocked and cannot proceed. Then summarize the ticket id, which tools you called, and the outcome.
"""

NUDGE = """Please continue the Kanban loop from where you left off. Call the remaining MCP tools now. Do not only describe the next steps. If a ticket already exists for "Add a shout option to greet", reuse it. Finish by moving it to done after tests pass and a review note is added. Also call mcp__openmausbot__list_bots if you have not already.
"""


def log(event: str, **data: Any) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **data}
    STATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STATE_LOG.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    brief = json.dumps(data, default=str)
    if len(brief) > 500:
        brief = brief[:500] + "..."
    print(f"[{rec['ts']}] {event} {brief}", flush=True)


def latest_token() -> str:
    logs = sorted(Path("/workspace/dsh-superagent-prototype/.artifacts").glob("dsh-superagent-superagent-*.log"),
                  key=lambda p: p.stat().st_mtime)
    if not logs:
        raise SystemExit("no dsh log with token")
    text = logs[-1].read_text()
    m = re.search(r"token=(\S+)", text)
    if not m:
        raise SystemExit(f"no token in {logs[-1]}")
    return m.group(1)


def auth() -> str:
    token = latest_token()
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(f"{DSH}/?token={token}", timeout=20)
    cookies = list(jar)
    if not cookies:
        raise SystemExit("dsh auth did not set a cookie")
    return "; ".join(f"{c.name}={c.value}" for c in cookies)


def rpc(cookie: str, endpoint: str, payload: dict, timeout: int = 60) -> dict:
    body = {
        "type": "client-request",
        "rpcId": str(uuid.uuid4()),
        "method": endpoint,
        "payload": payload,
    }
    req = urllib.request.Request(
        f"{DSH}/api/{endpoint}",
        data=json.dumps(body).encode(),
        headers={
            "Host": "127.0.0.1:3082",
            "Cookie": cookie,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{endpoint} HTTP {e.code}: {raw[:800]}") from e
    result = data.get("result") or {}
    if not result.get("ok"):
        err = result.get("error") or data
        raise RuntimeError(f"{endpoint} failed: {err}")
    return result.get("value")


def read_session_events(session_id: str) -> list[dict]:
    path = SESSION_ROOT / session_id / "session.v4.jsonl.zstd"
    if not path.exists():
        return []
    dctx = zstandard.ZstdDecompressor()
    try:
        with path.open("rb") as f:
            with dctx.stream_reader(f) as reader:
                text = reader.read().decode("utf-8", "replace")
    except Exception as e:
        log("log_read_error", error=str(e), path=str(path))
        return []
    events = []
    for line in text.splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def extract_tool_calls(events: list[dict]) -> list[dict]:
    """Pull tool calls/results out of session events and assistant streams."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    def walk(obj: Any, typ: str | None = None, seq: Any = None) -> None:
        if isinstance(obj, dict):
            t = obj.get("type") or typ
            name = obj.get("name") or obj.get("tool") or obj.get("toolName") or obj.get("tool_name")
            # Common DSH/LLM stream shapes
            if t and "tool" in str(t).lower():
                add({
                    "type": t,
                    "seq": obj.get("seq", seq),
                    "name": name,
                    "id": obj.get("id") or obj.get("toolCallId") or obj.get("tool_call_id"),
                    "arguments": obj.get("arguments") or obj.get("args") or obj.get("input"),
                    "result": obj.get("result") or obj.get("output") or obj.get("content"),
                    "error": obj.get("error") or obj.get("failure"),
                    "status": obj.get("status") or obj.get("isError"),
                })
            if name and isinstance(name, str) and name.startswith("mcp__"):
                add({
                    "type": t or "named-mcp",
                    "seq": obj.get("seq", seq),
                    "name": name,
                    "id": obj.get("id") or obj.get("toolCallId"),
                    "arguments": obj.get("arguments") or obj.get("args") or obj.get("input"),
                    "result": obj.get("result") or obj.get("output") or obj.get("content"),
                    "error": obj.get("error") or obj.get("failure"),
                    "status": obj.get("status") or obj.get("isError"),
                })
            for v in obj.values():
                walk(v, t, obj.get("seq", seq))
        elif isinstance(obj, list):
            for x in obj:
                walk(x, typ, seq)

    for ev in events:
        walk(ev)
    return found


def ticket_snapshot() -> dict:
    cols = {}
    for col in ("backlog", "to-do", "planning", "in-progress", "in-review", "done"):
        d = TICKETS / col
        cols[col] = sorted(p.name for p in d.glob("*.md")) if d.exists() else []
    shout = []
    for col, names in cols.items():
        for n in names:
            if "shout" in n.lower() or "greet" in n.lower():
                shout.append(f"{col}/{n}")
    return {"columns": cols, "shout_like": shout}


def git_state() -> dict:
    import subprocess
    def run(args: list[str]) -> str:
        return subprocess.check_output(args, cwd=str(WORKTREE), text=True).strip()
    return {
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "log": run(["git", "log", "--oneline", "-5"]),
        "status": run(["git", "status", "--porcelain"]),
        "head": run(["git", "rev-parse", "HEAD"]),
    }


def text_from_events(events: list[dict]) -> str:
    parts = []
    for ev in events:
        typ = ev.get("type")
        if typ in ("assistant/message", "assistant/attempt"):
            blob = json.dumps(ev)
            # collect text chunks
            def texts(o):
                out = []
                if isinstance(o, dict):
                    if o.get("type") in ("text", "output_text") and isinstance(o.get("text"), str):
                        out.append(o["text"])
                    for v in o.values():
                        out.extend(texts(v))
                elif isinstance(o, list):
                    for x in o:
                        out.extend(texts(x))
                return out
            parts.extend(texts(ev))
        elif typ == "turn/end":
            parts.append(f"[turn/end {ev.get('data')}]")
    return "\n".join(parts)


def running_from_list(cookie: str, session_id: str) -> bool | None:
    try:
        val = rpc(cookie, "session/list", {"args": {"_request": {}}})
    except Exception:
        try:
            val = rpc(cookie, "session/list", {"args": {"_request": {"cursor": None}}})
        except Exception as e:
            log("list_error", error=str(e))
            return None
    items = val.get("items") or []
    for it in items:
        if it.get("sessionId") == session_id:
            return bool(it.get("running"))
    return None


def main() -> int:
    STATE_LOG.write_text("") if STATE_LOG.exists() else None
    cookie = auth()
    catalog = rpc(cookie, "session/modelCatalog", {"args": {}})
    log("model_catalog_default", default=catalog.get("default"), routable=catalog.get("routableProviders"))
    created = rpc(cookie, "session/create", {"args": {"request": {"cwd": CWD}}})
    sid = created["sessionId"]
    log("session_created", sessionId=sid, agentPreset=created.get("agentPreset"))
    selected = rpc(cookie, "session/selectModel", {
        "args": {"request": {"sessionId": sid, "provider": PROVIDER, "model": MODEL}}
    })
    log("model_selected", selected=selected)

    # Confirm seed events
    time.sleep(0.4)
    evs = read_session_events(sid)
    seed = {e.get("type"): e.get("data") for e in evs}
    log("session_seed", approval=seed.get("approval/policy"), sandbox=seed.get("sandbox/mode"),
        preset=seed.get("permission/preset"), model=seed.get("model/selection"))

    accepted = rpc(cookie, "session/prompt", {
        "args": {"request": {
            "requestId": str(uuid.uuid4()),
            "sessionId": sid,
            "mode": "queue",
            "content": [{"type": "text", "text": PROMPT}],
            "clientTimeZone": "Asia/Tokyo",
        }}
    })
    log("prompt_accepted", accepted=accepted)
    started = time.time()
    deadline = started + 28 * 60
    last_event_count = 0
    last_change = time.time()
    last_tools: list[dict] = []
    nudges = 0
    max_nudges = 3
    turn_ends = 0
    last_turn_ends = 0

    while time.time() < deadline:
        time.sleep(5)
        evs = read_session_events(sid)
        tools = extract_tool_calls(evs)
        snap = ticket_snapshot()
        types = [e.get("type") for e in evs]
        turn_ends = types.count("turn/end")
        running = running_from_list(cookie, sid)

        if len(evs) != last_event_count or tools != last_tools or turn_ends != last_turn_ends:
            last_change = time.time()
            # log only new event types / new tools
            new_evs = evs[last_event_count:]
            new_types = [e.get("type") for e in new_evs]
            # compact errors
            errors = []
            for e in new_evs:
                if e.get("type") in ("llm/retry", "turn/end", "assistant/attempt"):
                    blob = json.dumps(e, default=str)
                    if any(s in blob for s in ("error", "RATE_LIMIT", "429", "failure", "MISSING")):
                        errors.append(blob[:400])
            log("progress", events=len(evs), new_types=new_types, running=running,
                tools=[{"type": t.get("type"), "name": t.get("name"), "error": t.get("error")} for t in tools],
                tickets=snap["shout_like"] or snap["columns"], errors=errors[:4])
            last_event_count = len(evs)
            last_tools = tools
            last_turn_ends = turn_ends

        # Success: shout ticket in done AND some MCP activity
        if any(x.startswith("done/") and "shout" in x for x in snap["shout_like"]):
            if running is False or (turn_ends and running is not True):
                # wait a moment for a final assistant message
                if running is False or (time.time() - last_change) > 8:
                    log("loop_complete", tickets=snap)
                    break

        idle = time.time() - last_change
        # Nudge if a turn ended and nothing new for a while, ticket not done
        done = any(x.startswith("done/") and "shout" in x for x in snap["shout_like"])
        if (not done) and (running is False) and turn_ends > 0 and idle > 45 and nudges < max_nudges:
            nudges += 1
            log("nudge", n=nudges, idle_s=int(idle), reason="turn ended and ticket not done")
            try:
                rpc(cookie, "session/prompt", {"args": {"request": {
                    "requestId": str(uuid.uuid4()),
                    "sessionId": sid,
                    "mode": "queue",
                    "content": [{"type": "text", "text": NUDGE}],
                    "clientTimeZone": "Asia/Tokyo",
                }}})
                last_change = time.time()
            except Exception as e:
                log("nudge_failed", error=str(e))
        elif (not done) and running is not True and idle > 180 and nudges < max_nudges and len(evs) > 5:
            nudges += 1
            log("nudge", n=nudges, idle_s=int(idle), reason="no new events for 3 minutes")
            try:
                rpc(cookie, "session/prompt", {"args": {"request": {
                    "requestId": str(uuid.uuid4()),
                    "sessionId": sid,
                    "mode": "queue",
                    "content": [{"type": "text", "text": NUDGE}],
                    "clientTimeZone": "Asia/Tokyo",
                }}})
                last_change = time.time()
            except Exception as e:
                log("nudge_failed", error=str(e))

    evs = read_session_events(sid)
    tools = extract_tool_calls(evs)
    snap = ticket_snapshot()
    try:
        git = git_state()
    except Exception as e:
        git = {"error": str(e)}

    # Collect assistant text and errors more completely
    turn_end_reasons = [e.get("data") for e in evs if e.get("type") == "turn/end"]
    retries = [e.get("data", {}).get("failure", {}) for e in evs if e.get("type") == "llm/retry"]
    model_events = [e.get("data") for e in evs if e.get("type") == "model/selection"]

    summary = {
        "sessionId": sid,
        "model": MODEL,
        "duration_s": int(time.time() - started),
        "nudges": nudges,
        "event_types": {t: sum(1 for e in evs if e.get("type") == t) for t in sorted({e.get("type") for e in evs})},
        "tool_calls": tools,
        "tickets": snap,
        "git": git,
        "turn_end_reasons": turn_end_reasons,
        "retries": retries[:8],
        "model_events": model_events,
        "shout_ticket_path": next((x for x in snap["shout_like"] if x.startswith("done/")), snap["shout_like"]),
    }
    # If a shout ticket exists, include its text (no secrets expected)
    for rel in snap["shout_like"]:
        p = TICKETS / rel.split("/", 1)[0] / rel.split("/", 1)[1]
        if p.exists():
            summary.setdefault("ticket_bodies", {})[rel] = p.read_text()[:4000]
    SUMMARY.write_text(json.dumps(summary, indent=2, default=str))
    log("finished", summary_path=str(SUMMARY), sessionId=sid, tickets=snap["shout_like"],
        tool_count=len(tools), git_head=git.get("head"), git_log=git.get("log"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
