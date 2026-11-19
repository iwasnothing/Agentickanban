"""Agentic Kanban web UI (demo prototype).

FastAPI app serving a Jira-like board over the Markdown ticket store, an invite-code
gate, a health panel (Superagent / OpenMausBot / Simular box VM) and a "+ New task"
endpoint. The user supplies only a high-level description; the DSH Superagent authors
the ticket fields, assigns a worker, and (by default) runs the manager→worker loop
in a background thread.

Secrets (invite code, tokens, keys) are never logged or returned.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ROOT = Path(os.environ.get("AIBOTS_CONTROL_ROOT", "/workspace/ai-bot-system")).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AIBOTS_CONTROL_ROOT", str(ROOT))

from scripts.config import COLUMNS  # noqa: E402
from scripts.ticket_sync import md_utils as md  # noqa: E402
from scripts.ticket_sync.store import TicketStore  # noqa: E402
from task_request import (  # noqa: E402
    build_superagent_prompt, job_watch_seconds, normalize_new_task,
)

WEBUI = Path(__file__).resolve().parent
STATIC = WEBUI / "static"
STATE = ROOT / ".state"
STATE.mkdir(parents=True, exist_ok=True)
JOBS_FILE = STATE / "webui-jobs.json"
LOG_FILE = STATE / "webui.log"

DSH = "http://127.0.0.1:3082"
DSH_MODEL = os.environ.get("KANBAN_DSH_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
DSH_PROVIDER = "openrouter"
DSH_SESSION_ROOT = Path("/home/box/.dsh/sessions/--workspace-ai-bot-system--")
OMB = "http://127.0.0.1:8799"
BOX_MCP = "https://mcp.box.simular.ai/mcp"

MAX_PER_HOUR = int(os.environ.get("KANBAN_MAX_TASKS_PER_HOUR", "4"))
SESSION_MAX_AGE = 7 * 24 * 3600
COOKIE = "ak_session"

# ---------------------------------------------------------------- secrets

def _secrets_dir() -> Path:
    return Path(os.environ.get("KANBAN_SECRETS_DIR", "/workspace/.secrets"))


def _invite_code() -> str:
    code = os.environ.get("KANBAN_INVITE_CODE", "").strip()
    if code:
        return code
    f = Path(os.environ.get("KANBAN_INVITE_CODE_FILE", str(_secrets_dir() / "kanban-invite-code")))
    if f.exists():
        return f.read_text().strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    code = secrets.token_urlsafe(12)
    old = os.umask(0o077)
    try:
        f.write_text(code + "\n")
    finally:
        os.umask(old)
    os.chmod(f, 0o600)
    return code


def _session_secret() -> str:
    f = Path(os.environ.get("KANBAN_WEBUI_SESSION_KEY_FILE", str(_secrets_dir() / "kanban-webui-session.key")))
    if f.exists():
        return f.read_text().strip()
    old = os.umask(0o077)
    try:
        f.write_text(secrets.token_hex(32))
    finally:
        os.umask(old)
    return f.read_text().strip()


INVITE_CODE = _invite_code()
SERIALIZER = URLSafeTimedSerializer(_session_secret(), salt="ak-session")

SECRET_RE = re.compile(
    r"(?i)(?:github_pat_|ghp_|gho_|ghu_|ghs_|ghr_|sk-or-v1-|sk-or-|Bearer\s+)[A-Za-z0-9._\-]+"
    r"|(?:GITHUB_TOKEN|SIMULAR_BOX_TOKEN|OPENROUTER[A-Z0-9_]*|BOX_TOKEN|KANBAN_INVITE_CODE|AUTHORIZATION)\s*[:=]\s*\S+"
)


def redact(s: Any) -> str:
    s = str(s)
    s = SECRET_RE.sub("***", s)
    if INVITE_CODE and len(INVITE_CODE) >= 4:
        s = s.replace(INVITE_CODE, "***")
    return s


def log(event: str, **data: Any) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event,
           **{k: redact(v)[:1000] for k, v in data.items()}}
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(rec) + "\n")

# ---------------------------------------------------------------- tickets / bots

_store: TicketStore | None = None


def store() -> TicketStore:
    global _store
    if _store is None:
        _store = TicketStore()
    return _store


def load_bots() -> list[dict]:
    reg = yaml.safe_load((ROOT / "bots/registry.yaml").read_text()) or {}
    out = []
    for bid, b in (reg.get("bots") or {}).items():
        out.append({
            "id": bid, "name": b.get("name") or bid,
            "omb_name": b.get("openmausbot_bot_name"), "omb_id": b.get("openmausbot_bot_id"),
            "model": b.get("model"), "branch": b.get("branch"), "enabled": b.get("enabled", True),
        })
    return out


def _issue_key(meta: dict, idx: int) -> str:
    gi = meta.get("github_issue")
    return f"AK-{gi}" if gi else f"AK-L{idx}"


def _parse_log(body: str) -> list[dict]:
    secs = md.sections(body)
    entries, cur = [], None
    for line in (secs.get("Execution Log") or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = {"heading": m.group(1), "lines": []}
            entries.append(cur)
        elif line.strip() and cur is not None:
            cur["lines"].append(line.strip().lstrip("- ").strip())
    return entries


def all_tickets() -> list[dict]:
    s = store()
    rows = []
    for col in COLUMNS:
        for p in sorted((s.s.tickets_dir / col).glob("*.md")):
            try:
                meta, body = md.parse(p.read_text(encoding="utf-8"))
            except Exception as e:  # keep the board alive
                rows.append({"id": p.stem, "title": p.stem, "status": col, "error": str(e)})
                continue
            meta["status"] = col
            ac = md.acceptance_criteria(body)
            rows.append({
                "id": meta.get("id") or p.stem, "title": meta.get("title") or p.stem, "status": col,
                "assignee_bot": meta.get("assignee_bot"), "priority": meta.get("priority") or "medium",
                "labels": meta.get("labels") or [], "github_issue": meta.get("github_issue"),
                "created_at": str(meta.get("created_at") or ""), "updated_at": str(meta.get("updated_at") or ""),
                "ac_total": len(ac), "ac_done": sum(1 for a in ac if a["done"]),
                "_mtime": p.stat().st_mtime,
            })
    # local keys for tickets without a GitHub issue, by creation order
    local = sorted([r for r in rows if not r.get("github_issue")], key=lambda r: (r.get("created_at"), r["id"]))
    lidx = {r["id"]: i + 1 for i, r in enumerate(local)}
    for r in rows:
        r["key"] = _issue_key(r, lidx.get(r["id"], 0))
        r["github_url"] = (f"https://github.com/{_gh_repo()}/issues/{r['github_issue']}" if r.get("github_issue") else None)
    return rows


def _gh_repo() -> str:
    return os.environ.get("GITHUB_REPO", "iwasnothing/Agentickanban")

# ---------------------------------------------------------------- health

_health: dict[str, dict] = {}
_box_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _http_probe(url: str, ok_codes=(200, 204, 401, 403), timeout=4.0) -> tuple[bool, int | None, float, str]:
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Host": url.split("/")[2]}), timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        return False, None, (time.time() - t0) * 1000, type(e).__name__
    return code in ok_codes, code, (time.time() - t0) * 1000, f"HTTP {code}"


def _tmux_alive(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode == 0


def _pgrep(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def check_superagent() -> dict:
    ok, code, ms, detail = _http_probe(DSH + "/")
    tmux = _tmux_alive("dsh-superagent")
    status = "green" if ok and tmux else ("amber" if ok or tmux else "red")
    return {"name": "Superagent", "sub": "DSH manager · 127.0.0.1:3082", "status": status, "latency_ms": round(ms),
            "detail": f"{detail} (401 = auth wall, up) · tmux {'alive' if tmux else 'missing'}",
            "model": DSH_MODEL}


def check_openmausbot() -> dict:
    ok, code, ms, detail = _http_probe(OMB + "/api/health", ok_codes=(200,))
    proc = _pgrep("openmausbot serve") or _pgrep("openmausbot/dist-server")
    bots = []
    try:
        with urllib.request.urlopen(OMB + "/api/bots", timeout=4) as r:
            bots = [b.get("name") for b in (json.loads(r.read()).get("bots") or [])]
    except Exception:
        pass
    status = "green" if ok and proc else ("amber" if ok or proc else "red")
    return {"name": "OpenMausBot", "sub": "worker harness · 127.0.0.1:8799", "status": status, "latency_ms": round(ms),
            "detail": f"{detail} · process {'running' if proc else 'missing'}" + (f" · bots: {', '.join(bots)}" if bots else "")}


async def _box_list() -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    token = os.environ.get("SIMULAR_BOX_TOKEN", "")
    if not token:
        raise RuntimeError("SIMULAR_BOX_TOKEN not set")
    async with streamablehttp_client(BOX_MCP, headers={"Authorization": "Bearer " + token}, timeout=15) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("box_list", {})  # read-only; never creates or wakes boxes
            if res.structuredContent:
                return res.structuredContent
            for c in res.content:
                if getattr(c, "text", None):
                    return json.loads(c.text)
    return {}


def check_box() -> dict:
    now = time.time()
    if _box_cache["v"] and now - _box_cache["t"] < 60:
        return _box_cache["v"]
    t0 = time.time()
    try:
        data = asyncio.run(asyncio.wait_for(_box_list(), timeout=25))
        ms = (time.time() - t0) * 1000
        boxes = data.get("boxes") or []
        b = next((x for x in boxes if x.get("box_id") == "cb-00a9be6c1672"), boxes[0] if boxes else None)
        if not b:
            v = {"status": "red", "detail": "no box found on the account", "state": "none"}
        else:
            state = b.get("state") or "unknown"
            st = "green" if state == "running" else ("amber" if state in ("hibernated", "starting", "waking", "queued", "saving") else "red")
            extra = " (wakes on demand when a bot needs it)" if state == "hibernated" else ""
            v = {"status": st, "state": state, "box_id": b.get("box_id"), "image": b.get("image"),
                 "detail": f"{b.get('name')} · {b.get('box_id')} · {state}{extra} · {b.get('image')}"}
        v.update({"latency_ms": round(ms)})
    except Exception as e:
        v = {"status": "red", "state": "error", "detail": "MCP check failed: " + redact(f"{type(e).__name__}: {e}")[:200],
             "latency_ms": round((time.time() - t0) * 1000)}
    v.update({"name": "Simular VM", "sub": "box MCP · mcp.box.simular.ai", "checked_at": time.time()})
    _box_cache.update(t=now, v=v)
    return v


def health_loop() -> None:
    while True:
        for key, fn in (("superagent", check_superagent), ("openmausbot", check_openmausbot), ("box", check_box)):
            try:
                v = fn()
            except Exception as e:
                v = {"name": key, "status": "red", "detail": redact(e)[:200], "latency_ms": None}
            v.setdefault("checked_at", time.time())
            if key != "box":
                v["checked_at"] = time.time()
            _health[key] = v
        time.sleep(15)

# ---------------------------------------------------------------- Superagent jobs

_jobs_lock = threading.Lock()


def _load_jobs() -> list[dict]:
    try:
        return json.loads(JOBS_FILE.read_text())
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    tmp = JOBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs[-200:], indent=1))
    tmp.replace(JOBS_FILE)


def _update_job(jid: str, **fields) -> None:
    with _jobs_lock:
        jobs = _load_jobs()
        for j in jobs:
            if j["id"] == jid:
                j.update(fields)
                j["updated_at"] = time.time()
                j.setdefault("events", [])
                if "stage" in fields:
                    j["events"].append({"t": time.time(), "stage": fields["stage"], "msg": fields.get("message", "")})
        _save_jobs(jobs)


def _dsh_token_sources() -> list[str]:
    """Text blobs that may contain the live DSH UI token, newest-first.

    Prefer the running tmux pane: artifact logs are only updated when the start
    wrapper tees stdout, and a stale token from a previous process causes HTTP 401.
    """
    blobs: list[str] = []
    try:
        blobs.append(subprocess.check_output(
            ["tmux", "capture-pane", "-t", "dsh-superagent", "-p", "-J", "-S", "-300"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ))
    except Exception:
        pass
    art = Path("/workspace/dsh-superagent-prototype/.artifacts")
    if art.is_dir():
        logs = sorted(art.glob("dsh-superagent-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lp in logs[:8]:
            try:
                blobs.append(lp.read_text(errors="replace"))
            except Exception:
                pass
    return blobs


def _dsh_token() -> str:
    for blob in _dsh_token_sources():
        # Use the last token= in the blob (most recently printed URL).
        matches = re.findall(r"token=(\S+)", blob)
        if matches:
            return matches[-1].rstrip(")'\".,;]")
    raise RuntimeError("no DSH UI token found in the Superagent tmux pane or .artifacts logs")


def _dsh_cookie() -> str:
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(f"{DSH}/?token={_dsh_token()}", timeout=20)
    if not list(jar):
        raise RuntimeError("DSH auth did not set a cookie")
    return "; ".join(f"{c.name}={c.value}" for c in jar)


def _rpc(cookie: str, method: str, payload: dict, timeout: int = 60) -> Any:
    body = {"type": "client-request", "rpcId": str(uuid.uuid4()), "method": method, "payload": payload}
    req = urllib.request.Request(f"{DSH}/api/{method}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Host": "127.0.0.1:3082", "Cookie": cookie, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} HTTP {e.code}: {redact(e.read().decode('utf-8', 'replace'))[:300]}") from e
    result = data.get("result") or {}
    if not result.get("ok"):
        raise RuntimeError(f"{method} failed: {redact(result.get('error') or data)[:300]}")
    return result.get("value")


def _session_events(sid: str) -> list[dict]:
    import zstandard
    path = DSH_SESSION_ROOT / sid / "session.v4.jsonl.zstd"
    if not path.exists():
        return []
    try:
        with path.open("rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as rd:
            text = rd.read().decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _session_running(cookie: str, sid: str) -> bool | None:
    try:
        val = _rpc(cookie, "session/list", {"args": {"_request": {}}})
    except Exception:
        return None
    for it in (val or {}).get("items") or []:
        if it.get("sessionId") == sid:
            return bool(it.get("running"))
    return None


def _find_ticket_by_marker(marker: str) -> str | None:
    for col in COLUMNS:
        for p in (store().s.tickets_dir / col).glob("*.md"):
            try:
                if marker in p.read_text(encoding="utf-8"):
                    return p.stem
            except Exception:
                pass
    return None


def _build_prompt(req: dict, marker: str) -> str:
    return build_superagent_prompt(req, marker)


def _fallback_create(jid: str, req: dict, marker: str, why: str) -> str | None:
    """If the Superagent could not create the ticket, create it via the store and run rule-based triage."""
    from scripts.triage import triage
    try:
        desc = req.get("description") or req.get("objective") or req["title"]
        res = store().create(
            title=req["title"],
            objective="Superagent did not expand this request (fallback path). See Context for the user's high-level description.",
            requirements=None,
            acceptance_criteria=None,
            priority=req["priority"],
            labels=req["labels"],
            context=[
                f"Requested via the Agentic Kanban web UI ({marker}).",
                f"Created by the web UI fallback because the Superagent run failed: {why[:160]}",
                "High-level description:",
                desc,
            ],
        )
        tid = res["ticket_id"]
        tri = triage(store(), tid, apply=True)
        _update_job(jid, ticket_id=tid, stage="fallback",
                    message=f"Created via ticket store + rule-based triage → {tri.get('suggested_bot') or 'unassigned'}")
        return tid
    except Exception as e:
        _update_job(jid, stage="failed", status="failed", message="fallback failed: " + redact(e)[:200])
        return None


def run_job(jid: str, req: dict) -> None:
    marker = f"webui-request:{jid}"
    deadline = time.time() + job_watch_seconds(bool(req.get("run_loop")))
    try:
        _update_job(jid, status="running", stage="connecting", message="Connecting to the Superagent")
        cookie = _dsh_cookie()
        sid = _rpc(cookie, "session/create", {"args": {"request": {"cwd": str(ROOT)}}})["sessionId"]
        _rpc(cookie, "session/selectModel", {"args": {"request": {"sessionId": sid, "provider": DSH_PROVIDER, "model": DSH_MODEL}}})
        _rpc(cookie, "session/prompt", {"args": {"request": {
            "requestId": str(uuid.uuid4()), "sessionId": sid, "mode": "queue",
            "content": [{"type": "text", "text": _build_prompt(req, marker)}], "clientTimeZone": "Asia/Hong_Kong"}}})
        _update_job(jid, session_id=sid, stage="superagent", message="Superagent session started; creating & triaging")
        log("job_started", job=jid, session=sid)
    except Exception as e:
        log("job_dsh_error", job=jid, error=e)
        _fallback_create(jid, req, marker, f"DSH unavailable: {e}")
        _update_job(jid, status="done", stage="done", message="Done (fallback path)")
        return

    last_n, last_change, nudged, tid, seen_tools = 0, time.time(), False, None, set()
    while time.time() < deadline:
        time.sleep(5)
        evs = _session_events(sid)
        if len(evs) != last_n:
            last_n, last_change = len(evs), time.time()
            for e in evs:
                if e.get("type") != "tool/call":
                    continue
                d = e.get("data") if isinstance(e.get("data"), dict) else {}
                name = d.get("name") or d.get("toolName") or d.get("tool_name") or d.get("tool")
                if isinstance(name, str) and name.startswith("mcp__") and name not in seen_tools:
                    seen_tools.add(name)
                    _update_job(jid, stage="tool", message=name.replace("mcp__", "").replace("__", " → "))
        if not tid:
            tid = _find_ticket_by_marker(marker)
            if tid:
                _update_job(jid, ticket_id=tid, stage="ticket", message=f"Ticket {tid} created by Superagent")
        running = _session_running(cookie, sid)
        turn_ends = sum(1 for e in evs if e.get("type") == "turn/end")
        idle = time.time() - last_change
        if running is False and turn_ends and idle > 10:
            if not tid and not nudged:
                nudged = True
                _rpc(cookie, "session/prompt", {"args": {"request": {
                    "requestId": str(uuid.uuid4()), "sessionId": sid, "mode": "queue",
                    "content": [{"type": "text", "text": "Please continue: expand the high-level description into a complete ticket (objective, requirements, unchecked acceptance criteria), call mcp__kanban-tickets__ticket_create now, log the expansion, ticket_triage apply=true (assign bot-001), ticket_sync_github, then run the worker loop if instructed."}],
                    "clientTimeZone": "Asia/Hong_Kong"}}})
                _update_job(jid, stage="nudge", message="Nudged Superagent to create the ticket")
                last_change = time.time()
                continue
            break
    if not tid:
        tid = _find_ticket_by_marker(marker)
    if not tid:
        _fallback_create(jid, req, marker, "Superagent finished without creating the ticket (possibly model rate limit)")
    timed_out = time.time() >= deadline
    _update_job(jid, status="done", stage="done",
                message=("Timed out waiting for Superagent; ticket left as-is" if timed_out else "Superagent finished"))
    log("job_done", job=jid, ticket=tid, timed_out=timed_out)

# ---------------------------------------------------------------- app

app = FastAPI(title="Agentic Kanban", docs_url=None, redoc_url=None, openapi_url=None)
_login_attempts: dict[str, list[float]] = {}


def _authed(request: Request) -> bool:
    tok = request.cookies.get(COOKIE)
    if not tok:
        return False
    try:
        SERIALIZER.loads(tok, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    public = path in ("/login", "/api/login", "/favicon.ico", "/healthz") or path.startswith("/static/login")
    if not public and not _authed(request):
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # Non-empty body: the localtolink tunnel stalls on zero-length redirect responses.
        return HTMLResponse('<!doctype html><meta http-equiv="refresh" content="0;url=/login"><a href="/login">Sign in</a>',
                            status_code=303, headers={"Location": "/login"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/api/login")
async def login(request: Request):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.time()
    hist = [t for t in _login_attempts.get(ip, []) if now - t < 600]
    if len(hist) >= 10:
        return JSONResponse({"error": "too many attempts, try again later"}, status_code=429)
    hist.append(now)
    _login_attempts[ip] = hist
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str(body.get("code") or "").strip()
    if not code or not hmac.compare_digest(code.encode(), INVITE_CODE.encode()):
        log("login_failed", ip=ip)
        return JSONResponse({"error": "invalid invite code"}, status_code=401)
    log("login_ok", ip=ip)
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(COOKIE, SERIALIZER.dumps({"u": "invitee", "t": int(now)}), max_age=SESSION_MAX_AGE,
                    httponly=True, samesite="lax", secure=secure, path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/board")
def board():
    tickets = all_tickets()
    for t in tickets:
        t.pop("_mtime", None)
    return {"columns": COLUMNS, "bots": load_bots(), "tickets": tickets, "generated_at": time.time(),
            "repo": _gh_repo(), "project_url": "https://github.com/users/iwasnothing/projects/2"}


@app.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: str):
    try:
        t = store().get(ticket_id)
    except Exception as e:
        raise HTTPException(404, str(e))
    secs = t["sections"]
    meta = t["metadata"]
    key = next((x["key"] for x in all_tickets() if x["id"] == ticket_id), ticket_id)
    gi = meta.get("github_issue")
    return {
        "id": ticket_id, "key": key, "meta": {k: (str(v) if k.endswith("_at") else v) for k, v in meta.items()},
        "objective": secs.get("Objective", ""), "requirements": secs.get("Requirements", ""),
        "context": secs.get("Context", ""),
        "acceptance_criteria": t["acceptance_criteria"], "final_review": secs.get("Final Review", ""),
        "log": _parse_log(md.parse(t["markdown"])[1]),
        "github_url": f"https://github.com/{_gh_repo()}/issues/{gi}" if gi else None,
    }


@app.get("/api/health")
def health():
    return {"services": [_health.get(k) for k in ("superagent", "openmausbot", "box") if _health.get(k)],
            "now": time.time()}


def _clean_list(v: Any, n: int, ln: int) -> list[str]:
    if isinstance(v, str):
        v = v.splitlines()
    out = []
    for x in (v or []):
        x = re.sub(r"^\s*[-*]\s*(\[[ xX]\]\s*)?", "", str(x)).strip()
        if x:
            out.append(x[:ln])
    return out[:n]


@app.get("/api/jobs")
def jobs():
    js = _load_jobs()[-20:]
    hour = [j for j in _load_jobs() if time.time() - j.get("created_at", 0) < 3600]
    inflight = any(j.get("status") in ("queued", "running") for j in _load_jobs())
    return {"jobs": list(reversed(js)), "limit_per_hour": MAX_PER_HOUR, "used_this_hour": len(hour), "in_flight": inflight}


@app.post("/api/tasks")
async def create_task(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "invalid JSON")
    req, errs = normalize_new_task(body)
    if errs or req is None:
        return JSONResponse({"error": "; ".join(errs or ["invalid request"])}, status_code=422)
    with _jobs_lock:
        js = _load_jobs()
        if any(j.get("status") in ("queued", "running") and time.time() - j.get("created_at", 0) < 3600 for j in js):
            return JSONResponse({"error": "The Superagent is already working on a request. Try again when it finishes."}, status_code=429)
        if len([j for j in js if time.time() - j.get("created_at", 0) < 3600]) >= MAX_PER_HOUR:
            return JSONResponse({"error": f"Hourly limit reached ({MAX_PER_HOUR} tasks/hour). Try again later."}, status_code=429)
        jid = uuid.uuid4().hex[:10]
        title = req["title"]
        js.append({"id": jid, "title": title, "status": "queued", "stage": "queued", "message": "Queued for Superagent",
                   "run_loop": req["run_loop"], "created_at": time.time(), "updated_at": time.time(), "events": [],
                   "ticket_id": None})
        _save_jobs(js)
    log("job_queued", job=jid, title=title, run_loop=req["run_loop"])
    threading.Thread(target=run_job, args=(jid, req), daemon=True).start()
    return {"ok": True, "job_id": jid}


@app.on_event("startup")
def _startup():
    # mark jobs orphaned by a restart
    with _jobs_lock:
        js = _load_jobs()
        for j in js:
            if j.get("status") in ("queued", "running"):
                j["status"], j["stage"], j["message"] = "failed", "failed", "Interrupted by a web UI restart"
        _save_jobs(js)
    threading.Thread(target=health_loop, daemon=True).start()
