#!/usr/bin/env python3
"""Live driver: Superagent runs the Kanban loop for EXISTING ticket
2026-11-14_ui-smoke-test-add-a-greet-many-helper (AK-5 / issue #5).

Does not create a new ticket. Does not implement the code itself.
Logs under .state/ (does not overwrite prior e2e reports).
"""
from __future__ import annotations

import json
import re
import subprocess
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
SESSION_ROOT = Path("/home/box/.dsh/sessions/--workspace-ai-bot-system--")
TICKETS = Path("/workspace/ai-bot-system/tickets")
WORKTREE = Path("/workspace/aibots/workspaces/bot-001")
STATE_LOG = Path("/workspace/ai-bot-system/.state/e2e-ak5-greet-many-20261114-1051-journey.jsonl")
SUMMARY = Path("/workspace/ai-bot-system/.state/e2e-ak5-greet-many-20261114-1051-summary.json")
TICKET_ID = "2026-11-14_ui-smoke-test-add-a-greet-many-helper"
MAUSBOT_OMB_ID = "8a7348b7-596f-4a9a-8c13-fa57a262ceaf"
BOX_ID = "cb-00a9be6c1672"

PROMPT = """You are the Kanban manager for this box. Drive the work yourself with your MCP tools. Do not only describe the plan — call the tools. Do not implement the code yourself, and do not create a new ticket.

First, call mcp__openmausbot__list_bots once to confirm OpenMausBot is reachable. The Kanban worker is bot-001, which maps to OpenMausBot bot MausBot (id 8a7348b7-596f-4a9a-8c13-fa57a262ceaf).

Then run the full ticket loop for the EXISTING ticket below. Use the kanban MCP tools (names use underscores, not dots):

Ticket id (reuse this; do not call ticket_create): 2026-11-14_ui-smoke-test-add-a-greet-many-helper
Path: tickets/to-do/2026-11-14_ui-smoke-test-add-a-greet-many-helper.md
GitHub issue: https://github.com/iwasnothing/Agentickanban/issues/5 (already live)
Project item: PVTI_lAHOAlPnVM4BrZECzhInbE8 (currently To-Do)
Title: UI smoke test: add a greet_many helper
Objective: In the bot-001 worktree (/workspace/aibots/workspaces/bot-001, branch bots/bot-001), add greet_many(names) in src/demo/greet.py that returns a list of greetings by calling greet() for each name, in order. greet_many([]) must return []. Do not change existing greet() or farewell() behavior unless required for this helper. Add or update unittest coverage in tests/test_greet.py.
Context: The worktree HEAD already has greet(name, formal=False, shout=False) and farewell(name). A leftover uncommitted times option from issue #3 was stashed as stash@{0}; do not pop that stash or treat issue #3 as this ticket. The main checkout /workspace/aibots/project-repo may be behind the worktree; the worker MUST use the bot-001 worktree. Implementation and tests MUST run on the Simular box remote VM via OpenMausBot's enabled MCP server named `box` (https://mcp.box.simular.ai/mcp). After tests pass on the VM, copy src/demo/greet.py and tests/test_greet.py back to the worktree and commit.
Acceptance criteria:
- greet_many(["Ada", "Bob"]) returns [greet("Ada"), greet("Bob")]
- greet_many([]) returns []
- all tests pass
Priority: low. Labels: python, backend. Already assigned to bot-001.

Required steps (do not skip):
1. mcp__kanban-tickets__ticket_get or ticket_list to load 2026-11-14_ui-smoke-test-add-a-greet-many-helper. Do not create a second ticket.
2. mcp__kanban-tickets__ticket_move to in-progress if it is not already there.
3. Try mcp__kanban-bots__bot_start_session for bot-001 and this ticket, then mcp__kanban-bots__bot_send_prompt with the implementation prompt below, wait for the turn, then bot_get_last_turn_diff / bot_run_tests / bot_commit_turn as needed.
   Known workaround: bot_start_session or bot_commit_turn can fail because MCP drops GIT_CONFIG_KEY_* but leaves GIT_CONFIG_COUNT (git error "missing config key GIT_CONFIG_KEY_0"). If that happens, do not spend the rest of the run only debugging git config. Switch to mcp__openmausbot__send_bot_message (bot_id 8a7348b7-596f-4a9a-8c13-fa57a262ceaf, or the MausBot id from list_bots) on an existing MausBot thread, then mcp__openmausbot__wait_for_conversation (and get_bot_messages if useful). MausBot can commit in the worktree itself.
4. The prompt you send to MausBot MUST require all of the following:
   - Implementation and tests MUST run on the Simular box remote VM using OpenMausBot's `box` MCP tools (box_ensure or box_wake, then shell / write_file / read_file / unittest on the VM).
   - Use the EXISTING box id cb-00a9be6c1672 (name: main). It may be hibernated; waking it is fine. Do NOT call box_create and do not create a new box.
   - Do not accept or perform a local-only edit that never used the VM.
   - After tests pass on the VM, copy src/demo/greet.py and tests/test_greet.py back to /workspace/aibots/workspaces/bot-001 and commit on branch bots/bot-001. Prefer a local commit (do not force-push; do not push unless the normal bot_commit_turn does a non-force push of the bot branch).
   - Do not print, echo, or log API keys, tokens, or Authorization headers. Do not set BOX_TOKEN to the Simular key. Do not commit .secrets, .env, or keys.
   - Do not pop the leftover times-option stash (stash@{0}), and do not work on issue #3 or any other ticket.
5. Review the result yourself. If tests fail, greet_many is missing, or there is no evidence the Simular VM was used (box_ensure / box_wake / box.shell / box.write_file / box.read_file on box cb-00a9be6c1672), send another prompt and retry a small number of times. Do not implement the patch yourself. Do not tick acceptance criteria unless the work really ran on the VM and meets them.
6. mcp__kanban-tickets__ticket_add_review_note describing completeness, whether the VM was used (cite box_* tools and the box id), test results, and the worktree commit. Tick or record acceptance criteria only if they are truly met.
7. mcp__kanban-tickets__ticket_move to in-review, then to done if acceptance is met.
8. After review, call mcp__kanban-tickets__ticket_sync_github for this ticket so issue #5 and the project Status update for real (live sync, not dry-run).

Keep going until the ticket is in done (or in-review with a review note if you are blocked from done), or you are blocked and cannot proceed. Then summarize the ticket id, tools you called, whether MausBot used the Simular box (box id and test output), the worktree commit, and the outcome.
"""

NUDGE = """Please continue the Kanban loop from where you left off. Call the remaining MCP tools now. Do not only describe the next steps. Reuse the existing ticket 2026-11-14_ui-smoke-test-add-a-greet-many-helper (do not create a new one, and do not work on issue #3). If bot_start_session or bot_commit_turn fails with missing GIT_CONFIG_KEY_0, switch to mcp__openmausbot__send_bot_message on the existing MausBot thread (bot id 8a7348b7-596f-4a9a-8c13-fa57a262ceaf) and let MausBot commit in the worktree. Implementation and tests must run on the existing Simular box cb-00a9be6c1672 via OpenMausBot's `box` MCP tools (wake it if hibernated; do not create a new box); do not accept a local-only edit. After review, move the ticket to in-review/done as warranted and call mcp__kanban-tickets__ticket_sync_github (live) so issue #5 updates. Also call mcp__openmausbot__list_bots if you have not already. Do not tick acceptance criteria unless the work really ran on the VM and meets them. Do not pop stash@{0}.
"""

SECRET_RE = re.compile(
    r"(?i)(?:github_pat_|ghp_|gho_|ghu_|ghs_|ghr_|sk-or-v1-|sk-or-|Bearer\s+)[A-Za-z0-9._\\-]+"
    r"|(?:GITHUB_TOKEN|SIMULAR_BOX_TOKEN|OPENROUTER[A-Z0-9_]*|BOX_TOKEN|AUTHORIZATION|DSH[_A-Z]*COOKIE|KANBAN_INVITE_CODE)"
    r"\s*[:=]\s*\S+"
    r"|(?:authorization\"\s*:\s*\"[^\"]+\")"
)


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        text = SECRET_RE.sub("***REDACTED***", obj)
        text = re.sub(r"(?i)(cookie\s*[:=]\s*)[^;\\s]+", r"\1***REDACTED***", text)
        return text
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(s in kl for s in ("token", "secret", "authorization", "cookie", "api_key", "apikey", "password")):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def log(event: str, **data: Any) -> None:
    data = redact(data)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **data}
    STATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STATE_LOG.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    brief = json.dumps(data, default=str)
    if len(brief) > 500:
        brief = brief[:500] + "..."
    print(f"[{rec['ts']}] {event} {brief}", flush=True)


def latest_token() -> str:
    logs = sorted(
        Path("/workspace/dsh-superagent-prototype/.artifacts").glob("dsh-superagent-superagent-*.log"),
        key=lambda p: p.stat().st_mtime,
    )
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
        raise RuntimeError(f"{endpoint} HTTP {e.code}: {redact(raw)[:800]}") from e
    result = data.get("result") or {}
    if not result.get("ok"):
        err = result.get("error") or data
        raise RuntimeError(f"{endpoint} failed: {redact(err)}")
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
            except Exception:
                pass
    return events


def extract_tool_calls(events: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        item = redact(item)
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    for ev in events:
        typ = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
        if typ in ("tool/call", "tool/result"):
            name = (
                data.get("name")
                or data.get("tool")
                or data.get("toolName")
                or data.get("tool_name")
            )
            add({
                "type": typ,
                "seq": ev.get("seq") or data.get("seq"),
                "name": name,
                "id": data.get("id") or data.get("toolCallId") or data.get("tool_call_id"),
                "error": data.get("error") or data.get("isError"),
            })
    return found


def find_ticket_path() -> Path | None:
    hits = list(TICKETS.glob(f"*/{TICKET_ID}.md"))
    return hits[0] if hits else None


def ticket_snapshot() -> dict:
    cols = {}
    times_like = []
    for col in ("backlog", "to-do", "planning", "in-progress", "in-review", "done"):
        files = sorted(p.name for p in (TICKETS / col).glob("*.md"))
        cols[col] = files
        for n in files:
            if TICKET_ID in n or "greet-many" in n or "greet_many" in n:
                times_like.append(f"{col}/{n}")
    return {"columns": {k: len(v) for k, v in cols.items()}, "times_like": times_like}


def ticket_column(snap: dict) -> str | None:
    for rel in snap.get("times_like") or []:
        if rel.endswith(f"{TICKET_ID}.md"):
            return rel.split("/", 1)[0]
    return None


def git_state() -> dict:
    def run(args: list[str]) -> str:
        return subprocess.check_output(args, cwd=str(WORKTREE), text=True).strip()

    return {
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "log": run(["git", "log", "--oneline", "-5"]),
        "status": run(["git", "status", "--porcelain"]),
        "head": run(["git", "rev-parse", "HEAD"]),
        "stash": run(["git", "stash", "list"]),
    }


def is_rate_limited(events: list[dict], errors: list[str]) -> str | None:
    blobs = errors[:]
    for e in events:
        if e.get("type") in ("llm/retry", "turn/end", "assistant/attempt", "llm/error"):
            blobs.append(json.dumps(e, default=str)[:800])
    text = "\n".join(blobs)
    if re.search(r"(?i)(daily.?cap|rate[_ -]?limit|429|free-models-per-day|exceeded.*quota)", text):
        return text[:1000]
    return None


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
    if STATE_LOG.exists():
        STATE_LOG.write_text("")
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

    time.sleep(0.4)
    evs = read_session_events(sid)
    seed = {e.get("type"): e.get("data") for e in evs}
    log(
        "session_seed",
        approval=seed.get("approval/policy"),
        sandbox=seed.get("sandbox/mode"),
        preset=seed.get("permission/preset"),
        model=seed.get("model/selection"),
    )

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
    deadline = started + 45 * 60
    last_event_count = 0
    last_change = time.time()
    last_tools: list[dict] = []
    nudges = 0
    max_nudges = 4
    turn_ends = 0
    last_turn_ends = 0
    blocker: str | None = None
    rate_limit_hits = 0

    while time.time() < deadline:
        time.sleep(5)
        evs = read_session_events(sid)
        tools = extract_tool_calls(evs)
        snap = ticket_snapshot()
        types = [e.get("type") for e in evs]
        turn_ends = types.count("turn/end")
        running = running_from_list(cookie, sid)
        col = ticket_column(snap)

        new_evs = evs[last_event_count:]
        errors = []
        for e in new_evs:
            if e.get("type") in ("llm/retry", "turn/end", "assistant/attempt", "llm/error"):
                blob = json.dumps(e, default=str)
                if any(s in blob for s in ("error", "RATE_LIMIT", "429", "failure", "MISSING", "quota", "daily")):
                    errors.append(redact(blob)[:400])

        if len(evs) != last_event_count or tools != last_tools or turn_ends != last_turn_ends:
            last_change = time.time()
            new_types = [e.get("type") for e in new_evs]
            tool_brief = []
            for t in tools[-40:]:
                tool_brief.append({"type": t.get("type"), "name": t.get("name"), "error": t.get("error")})
            log(
                "progress",
                events=len(evs),
                new_types=new_types,
                running=running,
                tools=tool_brief[-20:],
                ticket_column=col,
                tickets=snap["times_like"] or snap["columns"],
                errors=errors[:4],
            )
            last_event_count = len(evs)
            last_tools = tools
            last_turn_ends = turn_ends

        rl = is_rate_limited(new_evs, errors)
        if rl:
            rate_limit_hits += 1
            if re.search(r"(?i)(daily.?cap|free-models-per-day|exceeded.*quota)", rl) and running is False:
                blocker = "OpenRouter free-model daily cap or quota exceeded"
                log("blocked", reason=blocker, sample=rl[:400], rate_limit_hits=rate_limit_hits)
                break

        done = col == "done"
        reviewed = col in ("done", "in-review")
        if done and (running is False or (turn_ends and running is not True)):
            if running is False or (time.time() - last_change) > 8:
                log("loop_complete", tickets=snap, ticket_column=col)
                break

        idle = time.time() - last_change
        if (not done) and (running is False) and turn_ends > 0 and idle > 45 and nudges < max_nudges:
            if reviewed and idle > 60:
                log("loop_complete_in_review", tickets=snap, ticket_column=col, idle_s=int(idle))
                break
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
        elif (not done) and running is not True and idle > 180 and nudges >= max_nudges:
            blocker = "Superagent stalled after max nudges; ticket not in done"
            log("blocked", reason=blocker, ticket_column=col, idle_s=int(idle))
            break
    else:
        blocker = "deadline reached (45 minutes); Superagent loop stopped"
        log("blocked", reason=blocker, ticket_column=ticket_column(ticket_snapshot()))

    evs = read_session_events(sid)
    tools = extract_tool_calls(evs)
    snap = ticket_snapshot()
    try:
        git = git_state()
    except Exception as e:
        git = {"error": str(e)}

    turn_end_reasons = [redact(e.get("data")) for e in evs if e.get("type") == "turn/end"]
    retries = [redact(e.get("data", {}).get("failure", {})) for e in evs if e.get("type") == "llm/retry"]
    model_events = [redact(e.get("data")) for e in evs if e.get("type") == "model/selection"]

    ticket_bodies = {}
    tpath = find_ticket_path()
    if tpath:
        ticket_bodies[f"{tpath.parent.name}/{tpath.name}"] = tpath.read_text()[:8000]

    summary = {
        "sessionId": sid,
        "model": MODEL,
        "provider": PROVIDER,
        "ticket_id": TICKET_ID,
        "duration_s": int(time.time() - started),
        "nudges": nudges,
        "blocker": blocker,
        "rate_limit_hits": rate_limit_hits,
        "event_types": {t: sum(1 for e in evs if e.get("type") == t) for t in sorted({e.get("type") for e in evs})},
        "tool_call_count": sum(1 for t in tools if t.get("type") in ("tool/call", "tool_use")),
        "tool_calls": tools,
        "tickets": snap,
        "ticket_column": ticket_column(snap),
        "git": git,
        "turn_end_reasons": turn_end_reasons,
        "retries": retries[:12],
        "model_events": model_events,
        "ticket_bodies": ticket_bodies,
    }
    SUMMARY.write_text(json.dumps(redact(summary), indent=2, default=str))
    log(
        "finished",
        summary_path=str(SUMMARY),
        sessionId=sid,
        tickets=snap["times_like"],
        ticket_column=ticket_column(snap),
        tool_count=len(tools),
        tool_call_count=summary["tool_call_count"],
        git_head=git.get("head"),
        git_log=git.get("log"),
        blocker=blocker,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
