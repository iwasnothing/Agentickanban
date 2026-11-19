#!/usr/bin/env python3
"""Monitor/nudge Superagent for the HK residential price-per-sqft UI task.

Does not implement the analysis. Reuses the existing Superagent session
started by the web UI job. Logs under .state/ (does not overwrite prior e2e reports).
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
STATE = Path("/workspace/ai-bot-system/.state")
JOB_ID = "626bcd04ab"
SESSION_ID = "session-2f1e5200-5bb1-4504-9a08-8b86d57223c6"
MARKER = f"webui-request:{JOB_ID}"
MAUSBOT_OMB_ID = "8a7348b7-596f-4a9a-8c13-fa57a262ceaf"
BOX_ID = "cb-00a9be6c1672"
STAMP = "e2e-hk-prices-20261115-2011"
STATE_LOG = STATE / f"{STAMP}-journey.jsonl"
SUMMARY = STATE / f"{STAMP}-summary.json"
STDOUT_HINT = STATE / f"{STAMP}-monitor.pid"

NUDGE_TEMPLATE = """Please continue the Kanban loop from where you left off for the EXISTING ticket {ticket}. Call the remaining MCP tools now. Do not only describe the next steps. Do not create a new ticket. Do not implement the analysis yourself.

If bot_start_session or bot_commit_turn fails with missing GIT_CONFIG_KEY_0, switch to mcp__openmausbot__send_bot_message on MausBot (bot id 8a7348b7-596f-4a9a-8c13-fa57a262ceaf) and let MausBot commit in the worktree. Implementation and data work MUST run on the existing Simular box cb-00a9be6c1672 via OpenMausBot's `box` MCP tools (wake it if hibernated; do not create a new box). Do not accept a local-only run that never used the VM. If the VM has limited web access, download public datasets and then process them on the VM. Do not invent property-price figures.

Deliverables belong in /workspace/aibots/workspaces/bot-001/analysis/hk-residential-prices/ (script + chart images + README of sources, method, ranked table), committed on branch bots/bot-001. Do not change existing greet/farewell/greet_many/wave code. Do not pop stash@{{0}}. Do not work on issue #3, AK-5, or AK-6.

After work succeeds, add a review note (sources, ranked results, whether charts exist, whether the VM was used), move the ticket to in-review then done if complete, and call mcp__kanban-tickets__ticket_sync_github (live, not dry-run). Do NOT auto-tick acceptance checkboxes; they are human-in-the-loop.
"""

SECRET_RE = re.compile(
    r"(?i)(?:github_pat_|ghp_|gho_|ghu_|ghs_|ghr_|sk-or-v1-|sk-or-|Bearer\s+)[A-Za-z0-9._\\-]+"
    r"|(?:GITHUB_TOKEN|SIMULAR_BOX_TOKEN|OPENROUTER[A-Z0-9_]*|BOX_TOKEN|AUTHORIZATION|DSH[_A-Z]*COOKIE|KANBAN_INVITE_CODE|BRIDGE_SECRET|SESSION_SECRET)"
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
            if any(s in kl for s in ("token", "secret", "authorization", "cookie", "api_key", "apikey", "password", "invite")):
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
    if len(brief) > 600:
        brief = brief[:600] + "..."
    print(f"[{rec['ts']}] {event} {brief}", flush=True)


def latest_token() -> str:
    logs = sorted(
        Path("/workspace/dsh-superagent-prototype/.artifacts").glob("dsh-superagent-superagent-*.log"),
        key=lambda p: p.stat().st_mtime,
    )
    if not logs:
        raise SystemExit("no dsh log with token")
    text = logs[-1].read_text(errors="replace")
    m = re.search(r"token=(\S+)", text)
    if not m:
        raise SystemExit(f"no token in {logs[-1].name}")
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


def find_ticket_by_marker() -> tuple[str | None, Path | None]:
    for col in ("backlog", "to-do", "planning", "in-progress", "in-review", "done"):
        d = TICKETS / col
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if MARKER in text or "price-per-sqft" in text or "hk-residential-prices" in text:
                if "Hong Kong" in text or MARKER in text or "price-per-sqft" in p.name:
                    return p.stem, p
    return None, None


def ticket_snapshot(ticket_id: str | None) -> dict:
    cols = {}
    hits = []
    for col in ("backlog", "to-do", "planning", "in-progress", "in-review", "done"):
        files = sorted(p.name for p in (TICKETS / col).glob("*.md")) if (TICKETS / col).exists() else []
        cols[col] = files
        for n in files:
            if ticket_id and n.startswith(ticket_id):
                hits.append(f"{col}/{n}")
            elif "price-per-sqft" in n or "hk-residential" in n or "hong-kong-district" in n:
                hits.append(f"{col}/{n}")
    return {"columns": {k: len(v) for k, v in cols.items()}, "hits": hits}


def ticket_column(snap: dict) -> str | None:
    for rel in snap.get("hits") or []:
        return rel.split("/", 1)[0]
    return None


def load_job() -> dict:
    try:
        jobs = json.loads((STATE / "webui-jobs.json").read_text())
    except Exception:
        return {}
    for j in jobs:
        if j.get("id") == JOB_ID:
            return {k: v for k, v in j.items() if k != "events"} | {
                "n_events": len(j.get("events") or []),
                "last_event": (j.get("events") or [None])[-1],
            }
    return {}


def git_state() -> dict:
    def run(args: list[str]) -> str:
        return subprocess.check_output(args, cwd=str(WORKTREE), text=True).strip()

    analysis = WORKTREE / "analysis" / "hk-residential-prices"
    files = []
    if analysis.exists():
        for p in sorted(analysis.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(WORKTREE)))
    return {
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "log": run(["git", "log", "--oneline", "-6"]),
        "status": run(["git", "status", "--porcelain"]),
        "head": run(["git", "rev-parse", "HEAD"]),
        "stash": run(["git", "stash", "list"]),
        "analysis_files": files,
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


def tool_names(tools: list[dict]) -> list[str]:
    return [str(t.get("name") or "") for t in tools if t.get("name")]


def vm_used(tools: list[dict], evs: list[dict]) -> bool:
    blob = " ".join(tool_names(tools))
    if re.search(r"(?i)(box_ensure|box_wake|box_shell|box\.shell|box_write|box_read|box_exec|mcp__box__)", blob):
        return True
    text = json.dumps([e.get("type") for e in evs[-50:]])
    # also scan recent event payloads lightly (redacted later)
    for e in evs:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        name = str(d.get("name") or d.get("toolName") or "")
        if "box" in name.lower() and "openmaus" in name.lower() or name.startswith("mcp__box__"):
            return True
        args = json.dumps(d, default=str)
        if BOX_ID in args and re.search(r"(?i)box_(ensure|wake|shell|write|read|exec|run)", args):
            return True
    return BOX_ID in blob


def work_looks_complete(col: str | None, git: dict, tools: list[dict], evs: list[dict]) -> bool:
    if col not in ("done", "in-review"):
        return False
    files = git.get("analysis_files") or []
    has_chart = any(p.endswith((".png", ".svg", ".jpg", ".jpeg", ".pdf", ".webp")) for p in files)
    has_readme = any("README" in p.upper() or p.endswith(".md") for p in files)
    has_script = any(p.endswith((".py", ".ipynb", ".r", ".js")) for p in files)
    return bool(files) and (has_chart or (has_readme and has_script)) and vm_used(tools, evs)


def tunnel_url() -> str:
    p = STATE / "kanban-tunnel-url.txt"
    try:
        return p.read_text().strip()
    except Exception:
        return ""


def main() -> int:
    STDOUT_HINT.write_text(str(os_getpid()) + "\n")
    cookie = auth()
    sid = SESSION_ID
    log("monitor_start", job_id=JOB_ID, session_id=sid, tunnel=tunnel_url(), job=load_job())

    started = time.time()
    # UI job started ~20:11; allow ~55 minutes total from now, plus leftover
    deadline = started + 55 * 60
    last_event_count = 0
    last_change = time.time()
    last_tools: list[dict] = []
    nudges = 0
    max_nudges = 6
    last_turn_ends = 0
    blocker: str | None = None
    rate_limit_hits = 0
    ticket_id: str | None = None
    ticket_path: Path | None = None

    while time.time() < deadline:
        time.sleep(8)
        evs = read_session_events(sid)
        tools = extract_tool_calls(evs)
        if not ticket_id:
            ticket_id, ticket_path = find_ticket_by_marker()
        else:
            # path may move between columns
            _, ticket_path = find_ticket_by_marker()
        snap = ticket_snapshot(ticket_id)
        types = [e.get("type") for e in evs]
        turn_ends = types.count("turn/end")
        running = running_from_list(cookie, sid)
        col = ticket_column(snap)
        job = load_job()

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
            tool_brief = [{"type": t.get("type"), "name": t.get("name"), "error": t.get("error")} for t in tools[-30:]]
            try:
                git = git_state()
            except Exception as e:
                git = {"error": str(e)}
            log(
                "progress",
                events=len(evs),
                new_types=new_types[-30:],
                running=running,
                tools=tool_brief[-20:],
                ticket_id=ticket_id,
                ticket_column=col,
                tickets=snap,
                job_status=job.get("status"),
                job_stage=job.get("stage"),
                job_message=job.get("message"),
                git_head=git.get("head"),
                git_status=git.get("status"),
                analysis_files=git.get("analysis_files"),
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
                log("blocked", reason=blocker, sample=redact(rl)[:400], rate_limit_hits=rate_limit_hits)
                break

        try:
            git = git_state()
        except Exception as e:
            git = {"error": str(e)}

        done_enough = work_looks_complete(col, git, tools, evs) or (
            col == "done" and (git.get("analysis_files") or []) and running is False
        )
        if done_enough and (running is False or (turn_ends and running is not True)):
            if running is False or (time.time() - last_change) > 10:
                log("loop_complete", tickets=snap, ticket_column=col, ticket_id=ticket_id)
                break

        idle = time.time() - last_change
        nudge_text = NUDGE_TEMPLATE.format(ticket=ticket_id or "(the ticket created for this UI request; find it via the webui-request marker)")

        # Don't nudge while Superagent is clearly still working
        should_nudge = False
        reason = ""
        if (not done_enough) and (running is False) and turn_ends > 0 and idle > 50 and nudges < max_nudges:
            if col == "done" and not (git.get("analysis_files") or []) and idle > 60:
                should_nudge, reason = True, "ticket marked done but deliverables missing — ask Superagent to finish properly"
            elif col in ("done", "in-review") and work_looks_complete(col, git, tools, evs):
                log("loop_complete_review", tickets=snap, ticket_column=col, idle_s=int(idle))
                break
            elif col in ("done", "in-review") and (git.get("analysis_files") or []) and vm_used(tools, evs) and idle > 60:
                log("loop_complete_in_review", tickets=snap, ticket_column=col, idle_s=int(idle))
                break
            else:
                should_nudge, reason = True, "turn ended and work not complete"
        elif (not done_enough) and running is not True and idle > 240 and nudges < max_nudges and len(evs) > 5:
            should_nudge, reason = True, "no new events for 4 minutes"

        if should_nudge:
            nudges += 1
            log("nudge", n=nudges, idle_s=int(idle), reason=reason, ticket_id=ticket_id, column=col)
            try:
                rpc(cookie, "session/prompt", {"args": {"request": {
                    "requestId": str(uuid.uuid4()),
                    "sessionId": sid,
                    "mode": "queue",
                    "content": [{"type": "text", "text": nudge_text}],
                    "clientTimeZone": "Asia/Tokyo",
                }}})
                last_change = time.time()
            except Exception as e:
                log("nudge_failed", error=str(e))
        elif (not done_enough) and running is not True and idle > 240 and nudges >= max_nudges:
            blocker = "Superagent stalled after max nudges; work not complete"
            log("blocked", reason=blocker, ticket_column=col, idle_s=int(idle), ticket_id=ticket_id)
            break
    else:
        blocker = "deadline reached (55 minutes); Superagent loop stopped"
        log("blocked", reason=blocker, ticket_column=ticket_column(ticket_snapshot(ticket_id)), ticket_id=ticket_id)

    evs = read_session_events(sid)
    tools = extract_tool_calls(evs)
    ticket_id, ticket_path = find_ticket_by_marker()
    snap = ticket_snapshot(ticket_id)
    try:
        git = git_state()
    except Exception as e:
        git = {"error": str(e)}
    job = load_job()

    ticket_body = ""
    if ticket_path and ticket_path.exists():
        ticket_body = ticket_path.read_text()[:12000]

    # Parse a few ticket fields without inventing data
    key = None
    gh_issue = None
    if ticket_body:
        m = re.search(r"^key:\s*(\S+)", ticket_body, re.M)
        if m:
            key = m.group(1)
        m = re.search(r"^github_issue:\s*(\S+)", ticket_body, re.M)
        if m:
            gh_issue = m.group(1)

    summary = {
        "job_id": JOB_ID,
        "sessionId": sid,
        "model": MODEL,
        "provider": PROVIDER,
        "ticket_id": ticket_id,
        "ticket_path": str(ticket_path) if ticket_path else None,
        "key": key,
        "github_issue": gh_issue,
        "ticket_column": ticket_column(snap),
        "tickets": snap,
        "job": job,
        "nudges": nudges,
        "blocker": blocker,
        "vm_used": vm_used(tools, evs),
        "box_id": BOX_ID,
        "tool_names": sorted(set(n for n in tool_names(tools) if n)),
        "n_events": len(evs),
        "git": git,
        "tunnel": tunnel_url(),
        "started_monitor_unix": started,
        "finished_hkt": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "elapsed_s": int(time.time() - started),
    }
    SUMMARY.write_text(json.dumps(redact(summary), indent=2, default=str) + "\n")
    # keep ticket body in a separate file
    if ticket_body:
        (STATE / f"{STAMP}-ticket-excerpt.md").write_text(ticket_body)
    log("monitor_finished", summary_path=str(SUMMARY), ticket_id=ticket_id, column=ticket_column(snap), blocker=blocker)
    print("SUMMARY_WRITTEN", SUMMARY, flush=True)
    return 0 if not blocker else 2


def os_getpid() -> int:
    import os
    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
