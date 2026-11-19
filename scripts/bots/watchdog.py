"""Superagent worker watchdog: periodically inspect every configured bot for stalls
and decide how to unblock them.

Stall kinds:
  * host_approval — a live, structured permission-gate needs_approval for a real
    host (gate-audit / pending-approvals from the worker). Ticket body, review
    notes, filenames, and numbers are never treated as hosts. A finished turn is
    not a stall; leftover log text after a result is ignored.
    OpenMausBot has no interactive approval card, so waiting for a human prompt
    hangs the ticket.
  * permission_gate — other needs_approval (package install, recursive rm, paths, …).
    Each pending request is risk-assessed: low-risk may be approved; high-risk
    is rejected with a counter-proposal. Never claim the gate approved a request
    unless it actually did.
  * no_progress / hung_session — a running or in-flight turn has gone silent.
  * source_failure — a finished worker turn reported a missing, 404, paid, or
    login-walled data source (detected from the last Turn result, never from
    leftover ticket prose). Only considered on in-progress tickets; a still-
    running turn is not interrupted.

Decisions (never invent data, never tick official AC, never mark Done to clear a stall):
  * allow_host + resume — host matches configured official/safe suffixes (default: gov.hk).
  * resume — host or command is already allowed; just wake the worker.
  * approve — low-risk pending command (e.g. pip install of well-known data libraries
    from the default index) is approved at the permission gate, then the worker is resumed
    if the turn is not still running.
  * deny / reject — high-risk host or command; fail clearly, suggest a counter-proposal
    that still serves the user's high-level intent, and never claim the gate approved it.
  * redirect — source failure with a clearly public/safe alternative matching the
    user's original high-level intent; brief the worker once to change direction.
  * note — record a reasoned note (hung session, paid/walled source) so the ticket
    does not hang silently. Paid/login-walled sources are never auto-approved.

CLI:
  python -m scripts.bots.watchdog once [--bot BOT] [--no-apply]
  python -m scripts.bots.watchdog loop [--interval N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from scripts.bots.approval_risk import assess_pending, claims_gate_approval
from scripts.bots.failure_advisor import (
    advise as default_advise,
    detect_source_failure,
    extract_intent,
    last_worker_result,
    prompt_in_flight,
    result_already_handled,
    SourceFailure,
)
from scripts.config import Settings, load_settings
from scripts.permission_gate.gate import (
    UNKNOWN_HOST_RE,
    PermissionGate,
    URL_RE,
    host_is_allowed,
    normalize_host,
)
from scripts.ticket_sync.store import TicketStore

DEFAULT_INTERVAL = 60
DEFAULT_NO_PROGRESS = 240
DEFAULT_HUNG = 720
DEFAULT_LOOKBACK = 1800
DEFAULT_SAFE_SUFFIXES = ["gov.hk"]

# Hostnames come only from structured gate events (URL / host field), never ticket prose.
_FILE_TLDS = frozenset({
    "json", "jsonl", "yaml", "yml", "py", "pyc", "csv", "tsv", "png", "jpg", "jpeg",
    "gif", "svg", "html", "htm", "md", "txt", "log", "js", "ts", "lock", "xls", "xlsx",
    "pdf", "zip", "xml", "toml", "ini", "cfg", "conf",
})
_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)


def is_structured_hostname(host: str | None) -> bool:
    """True only for a plausible DNS name. Numbers (9.1) and filenames (sources.json) are not hosts."""
    host = normalize_host(host or "")
    if not host or not _HOST_RE.fullmatch(host):
        return False
    if not re.search(r"[a-z]", host):
        return False
    tld = host.rsplit(".", 1)[-1]
    if tld in _FILE_TLDS or tld.isdigit():
        return False
    return True


ACTIVE_COLUMNS = ("planning", "in-progress", "in-review")

HOST_WAIT_RE = re.compile(
    r"(needs human approval|please approve|unknown host|when prompted|permission gate.*approv)",
    re.I,
)
TURN_PROGRESS_RE = re.compile(r"^## Turn \d+ progress \(([^)]+)\)\s*$", re.M)
TURN_RESULT_RE = re.compile(r"^## Turn (\d+) result \(([^)]+)\)\s*$", re.M)
TURN_SENT_RE = re.compile(r"^## Turn (\d+) \(([^)]+)\)\s*$", re.M)


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _iso_to_ts(value: str | None) -> float | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19] if "T" in value and fmt.endswith("%S") else value, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def load_watchdog_config(settings: Settings) -> dict[str, Any]:
    raw = dict((settings.raw or {}).get("watchdog") or {})
    suffixes = raw.get("safe_host_suffixes") or list(DEFAULT_SAFE_SUFFIXES)
    suffixes = [normalize_host(s) for s in suffixes if normalize_host(str(s))]
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_seconds": int(raw.get("interval_seconds", DEFAULT_INTERVAL)),
        "no_progress_seconds": int(raw.get("no_progress_seconds", DEFAULT_NO_PROGRESS)),
        "hung_seconds": int(raw.get("hung_seconds", DEFAULT_HUNG)),
        "pending_lookback_seconds": int(raw.get("pending_lookback_seconds", DEFAULT_LOOKBACK)),
        "safe_host_suffixes": suffixes,
        "auto_apply": bool(raw.get("auto_apply", True)),
        "resume_on_allow": bool(raw.get("resume_on_allow", True)),
        "redirect_on_source_failure": bool(raw.get("redirect_on_source_failure", True)),
        "redirect_columns": [str(c) for c in (raw.get("redirect_columns") or ["in-progress"])],
        "assess_pending_risk": bool(raw.get("assess_pending_risk", True)),
        "low_risk_pypi_packages": [str(x).strip() for x in (raw.get("low_risk_pypi_packages") or []) if str(x).strip()],
    }


def host_is_safe(host: str, suffixes: list[str]) -> bool:
    host = normalize_host(host)
    return bool(host) and host_is_allowed(host, set(suffixes))


def enabled_bots(settings: Settings) -> dict[str, dict]:
    out = {}
    for bot_id, info in (settings.registry() or {}).items():
        if info is None:
            continue
        if info.get("enabled", True) is False:
            continue
        out[bot_id] = dict(info)
    return out


@dataclass
class Stall:
    bot_id: str
    kind: str
    ticket_id: str | None = None
    session_id: str | None = None
    host: str | None = None
    command: str | None = None
    evidence: str = ""
    last_activity: str | None = None
    turn_active: bool = False
    source: str | None = None
    subtype: str | None = None

    def fingerprint(self) -> str:
        return (
            f"{self.bot_id}|{self.ticket_id or ''}|{self.kind}|"
            f"{self.host or self.subtype or ''}|{(self.command or '')[:120]}"
        )


@dataclass
class Decision:
    action: str  # allow_host | resume | approve | deny | reject | redirect | note | none
    reason: str
    stall: Stall | None = None
    applied: bool = False
    skipped: bool = False
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d



def briefing_for_decision(decision: Decision) -> str:
    """Worker briefing for a watchdog decision.

    Must not tell the worker that a request "has been approved" unless
    decision.details["gate_approved"] is True (the permission gate actually allows it).
    """
    stall = decision.stall
    host = stall.host if stall else None
    cmd = (stall.command or "").strip() if stall else ""
    cmd_show = cmd if len(cmd) <= 220 else cmd[:217] + "..."
    reason = decision.reason
    counter = (decision.details or {}).get("counter_proposal")
    gate_ok = bool((decision.details or {}).get("gate_approved"))
    if decision.action == "redirect":
        intent = (decision.details or {}).get("intent") or "the original ticket objective"
        suggestion = (decision.details or {}).get("suggestion") or reason
        evidence = (stall.evidence if stall else "") or "required data source unavailable"
        return (
            f"The previous turn reported that a required data source is unavailable ({evidence}). "
            f"The user's original high-level intent is: {intent}. "
            f"Superagent's watchdog suggests this official public alternative: {suggestion} "
            "Change direction accordingly on the EXISTING ticket. "
            "Do not invent figures. Do not copy leftover artifacts and call them fresh official results. "
            "Do not tick or shrink official acceptance-criteria checkboxes. "
            "If the alternative only partially covers the original criteria, document the gap and leave official AC unchecked. "
            "Do not use paid or login-walled sources, and do not start a new ticket."
        )
    if decision.action in ("approve", "allow_host") and not gate_ok:
        target = f"command `{cmd_show}`" if cmd_show and decision.action == "approve" else f"host {host}"
        return (
            f"Superagent's watchdog did NOT approve {target} ({reason}). "
            "The permission gate has not allowed this request. Do not continue as if it were approved. "
            "Do not invent figures. Do not tick official acceptance-criteria checkboxes. "
            "Do not start a new ticket."
        )
    if decision.action == "approve" and gate_ok:
        head = (
            f"The command `{cmd_show}` has been approved by Superagent's watchdog "
            f"after a low-risk assessment ({reason}). The permission gate now allows this exact command."
        )
    elif decision.action == "allow_host" and gate_ok:
        head = f"Host {host} has been allowed by Superagent's watchdog ({reason})."
    elif decision.action == "resume":
        if cmd_show and not host:
            head = f"The command `{cmd_show}` is already allowed. Do not wait for an approval prompt."
        else:
            head = f"Host {host} is already allowed. Do not wait for an approval prompt."
    elif decision.action == "deny":
        head = (
            f"Host {host} was NOT approved by Superagent's watchdog ({reason}). "
            "Do not try to work around the gate."
        )
        if counter:
            head += f" Counter-proposal: {counter}"
    elif decision.action == "reject":
        head = (
            f"The command `{cmd_show}` was NOT approved by Superagent's watchdog "
            f"(risk: high — {reason}). Do not retry it or work around the gate."
        )
        if counter:
            head += f" Counter-proposal: {counter}"
    else:
        head = f"Superagent's watchdog recorded a stall ({reason})."
        if counter:
            head += f" Counter-proposal: {counter}"
    return (
        f"{head} OpenMausBot is non-interactive: no approval prompt will appear. "
        "Continue the EXISTING ticket work now. Do not wait. Do not invent figures. "
        "Do not copy leftover artifacts and call them fresh official results. "
        "Do not tick official acceptance-criteria checkboxes. "
        "If another host or command is blocked, report the exact request and stop that step. "
        "Do not use paid or login-walled sources. Do not start a new ticket."
    )


class WorkerWatchdog:
    def __init__(self, settings: Settings | None = None, store: TicketStore | None = None,
                 sender: Callable[..., dict] | None = None,
                 advisor: Callable[..., object] | None = None):
        self.s = settings or load_settings()
        self.store = store or TicketStore(self.s)
        self.cfg = load_watchdog_config(self.s)
        self.sender = sender  # optional (bot_id, session_id, prompt) -> dict
        self.advisor = advisor or default_advise
        self.state_path = self.s.state_dir / "watchdog-state.json"

    # ----- state -----
    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"actions": []}
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"actions": []}

    def _save_state(self, data: dict) -> None:
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.state_path)

    def _stall_identity(self, stall: Stall | None, rec: dict | None = None) -> str:
        if rec is not None:
            kind = rec.get("kind") or ""
            host = rec.get("host") or rec.get("subtype") or ""
            cmd = (rec.get("command") or "")[:120] if kind == "permission_gate" else ""
            return f"{rec.get('bot_id') or ''}|{rec.get('ticket_id') or ''}|{kind}|{host or cmd}"
        assert stall is not None
        token = stall.host or stall.subtype or ""
        if stall.kind == "permission_gate":
            token = (stall.command or "")[:120]
        return f"{stall.bot_id}|{stall.ticket_id or ''}|{stall.kind}|{token}"

    def _action_key(self, stall: Stall) -> str:
        """Cooldown key: one pending host or one pending command, not a prompt burst."""
        return self._stall_identity(stall)

    def _recently_acted(self, stall: Stall, action: str, cooldown: int = 600) -> bool:
        key = self._action_key(stall)
        cutoff = time.time() - cooldown
        unblock = {"allow_host", "resume", "deny"}
        failure_acts = {"redirect", "note"}
        for rec in self._load_state().get("actions") or []:
            rec_key = self._stall_identity(None, rec)
            if rec_key != key:
                continue
            ts = _iso_to_ts(rec.get("at"))
            if not ts or ts < cutoff:
                continue
            # An allow already implies a resume; do not send a second prompt for the same host.
            # A source-failure note or redirect is one decision; do not follow with a burst.
            # A pending-approval approve/reject is one decision per command.
            approval_acts = {"approve", "reject", "allow_host", "resume", "deny"}
            if rec.get("action") == action or (action in unblock and rec.get("action") in unblock):
                return True
            if action in failure_acts and rec.get("action") in failure_acts:
                return True
            if stall.kind in ("permission_gate", "host_approval") and action in approval_acts and rec.get("action") in approval_acts:
                return True
        return False

    def _record_action(self, decision: Decision) -> None:
        data = self._load_state()
        actions = data.setdefault("actions", [])
        stall = decision.stall
        actions.append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "action": decision.action,
            "reason": decision.reason,
            "bot_id": stall.bot_id if stall else None,
            "ticket_id": stall.ticket_id if stall else None,
            "kind": stall.kind if stall else None,
            "host": stall.host if stall else None,
            "subtype": stall.subtype if stall else None,
            "command": (stall.command or "")[:120] if stall else None,
            "fingerprint": stall.fingerprint() if stall else None,
            "applied": decision.applied,
        })
        data["last_run"] = datetime.now().isoformat(timespec="seconds")
        data["actions"] = actions[-200:]
        self._save_state(data)

    # ----- sources -----
    def _sessions_for(self, bot_id: str, ticket_id: str | None = None) -> list[dict]:
        path = self.s.state_dir / "sessions.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        rows = []
        for sess in data.values() if isinstance(data, dict) else []:
            if not isinstance(sess, dict):
                continue
            if sess.get("bot_id") != bot_id:
                continue
            if ticket_id and sess.get("ticket_id") != ticket_id:
                continue
            rows.append(sess)
        rows.sort(key=lambda s: s.get("started_at") or "", reverse=True)
        return rows

    def _read_jsonl_since(self, path: Path, since: float) -> list[dict]:
        if not path.exists():
            return []
        out = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-400:]:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _iso_to_ts(rec.get("at"))
            if ts is not None and ts < since:
                continue
            out.append(rec)
        return out

    def _ticket_body(self, ticket_id: str) -> tuple[Path | None, str]:
        try:
            path = self.store.find(ticket_id)
        except Exception:
            return None, ""
        try:
            return path, path.read_text(encoding="utf-8")
        except OSError:
            return path, ""

    def _last_activity_ts(self, bot_id: str, ticket_id: str | None, body: str) -> float | None:
        times: list[float] = []
        if ticket_id:
            try:
                path = self.store.find(ticket_id)
                times.append(path.stat().st_mtime)
            except Exception:
                pass
        # ISO stamps in the log
        for m in re.finditer(r"at (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", body or ""):
            ts = _iso_to_ts(m.group(1) + ":00")
            if ts:
                times.append(ts)
        for rec in self._read_jsonl_since(self.s.state_dir / "gate-audit.jsonl", time.time() - self.cfg["pending_lookback_seconds"]):
            if rec.get("bot") == bot_id:
                ts = _iso_to_ts(rec.get("at"))
                if ts:
                    times.append(ts)
        return max(times) if times else None

    def _host_from_rec(self, rec: dict) -> str:
        host = normalize_host(rec.get("host") or "")
        if not host and rec.get("reason"):
            m = UNKNOWN_HOST_RE.search(rec.get("reason") or "")
            if m:
                host = normalize_host(m.group(1))
        if not host and rec.get("command"):
            urls = URL_RE.findall(rec.get("command") or "")
            if urls:
                from urllib.parse import urlparse
                host = normalize_host(urlparse(urls[0]).hostname or "")
        return host

    def _pending_host_stalls(self, bot_id: str, since: float) -> list[Stall]:
        stalls: list[Stall] = []
        seen: set[str] = set()
        latest_decision: dict[str, str] = {}
        gate = PermissionGate(bot_id, settings=self.s)
        allowed = gate.allowed_hosts()
        events: list[tuple[str, dict]] = []
        for path, source in (
            (self.s.state_dir / "pending-approvals.jsonl", "pending-approvals"),
            (self.s.state_dir / "gate-audit.jsonl", "gate-audit"),
        ):
            for rec in self._read_jsonl_since(path, since):
                if rec.get("bot") != bot_id:
                    continue
                events.append((source, rec))
        events.sort(key=lambda item: item[1].get("at") or "")
        events = [(source, rec) for source, rec in events
                  if not self._source_is_cli(rec) and not source.startswith("cli")]
        best_source: dict[str, str] = {}
        for source, rec in events:
            host = self._host_from_rec(rec)
            decision = rec.get("decision")
            if host and decision in ("allow", "deny", "needs_approval"):
                latest_decision[host] = decision
            src = rec.get("source") or source
            if host and self._source_is_worker({"source": src}):
                best_source[host] = src
        for source, rec in events:
            host = self._host_from_rec(rec)
            decision = rec.get("decision")
            if host and latest_decision.get(host) == "allow":
                continue
            if host and host_is_allowed(host, allowed):
                continue
            if source == "gate-audit" and decision not in (None, "needs_approval"):
                continue
            if not host:
                if (decision == "needs_approval") or (source == "pending-approvals" and not rec.get("host")):
                    if decision == "allow":
                        continue
                    # skip pending records that are actually host stalls already handled
                    if UNKNOWN_HOST_RE.search(rec.get("reason") or ""):
                        continue
                    cmd = (rec.get("command") or "")[:200]
                    key = "cmd:" + cmd
                    if key in seen:
                        continue
                    seen.add(key)
                    stalls.append(Stall(
                        bot_id=bot_id, kind="permission_gate", host=None, command=rec.get("command"),
                        evidence=f"{source}: {rec.get('reason') or 'needs_approval'}",
                        last_activity=rec.get("at"),
                        source=rec.get("source") or source,
                    ))
                continue
            if host in seen:
                continue
            if not is_structured_hostname(host):
                continue
            seen.add(host)
            src = best_source.get(host) or rec.get("source") or source
            stalls.append(Stall(
                bot_id=bot_id, kind="host_approval", host=host, command=rec.get("command"),
                evidence=f"{source}: {rec.get('reason') or rec.get('resolution') or host}",
                last_activity=rec.get("at"),
                source=src,
            ))
        return stalls

    def _ticket_stalls(self, bot_id: str, ticket: dict, since: float) -> list[Stall]:
        """Hung / no-progress only. Never parse the ticket body for hostnames.

        Host-approval stalls come exclusively from structured gate events
        (_pending_host_stalls). Leftover review notes, filenames, and numbers
        after a finished turn are ignored.
        """
        tid = ticket.get("id") or ticket.get("ticket_id")
        if not tid:
            return []
        path, body = self._ticket_body(tid)
        if not body:
            return []
        log = body
        idx = body.find("# Execution Log")
        if idx >= 0:
            log = body[idx:]
        headings = list(re.finditer(r"^## (.+)\s*$", log, re.M))
        turn_headings = [(m.start(), m.group(1)) for m in headings if m.group(1).startswith("Turn ")]
        turn_active = bool(turn_headings and "progress" in turn_headings[-1][1])
        if not turn_active:
            return []
        last_ts = self._last_activity_ts(bot_id, tid, log)
        age = (time.time() - last_ts) if last_ts else None
        sessions = self._sessions_for(bot_id, tid)
        session_id = sessions[0]["session_id"] if sessions else None
        stalls: list[Stall] = []
        if age is not None:
            if age >= self.cfg["hung_seconds"]:
                stalls.append(Stall(
                    bot_id=bot_id, kind="hung_session", ticket_id=tid, session_id=session_id,
                    evidence=f"turn still marked in progress but last activity was {int(age)}s ago",
                    last_activity=datetime.fromtimestamp(last_ts).isoformat(timespec="seconds") if last_ts else None,
                    turn_active=True,
                ))
            elif age >= self.cfg["no_progress_seconds"] and "no new tool activity" in log.split("## ")[-1]:
                stalls.append(Stall(
                    bot_id=bot_id, kind="no_progress", ticket_id=tid, session_id=session_id,
                    evidence=f"no new tool activity for {int(age)}s",
                    last_activity=datetime.fromtimestamp(last_ts).isoformat(timespec="seconds") if last_ts else None,
                    turn_active=True,
                ))
        return stalls

    def _source_failure_stalls(self, bot_id: str, ticket: dict) -> list[Stall]:
        """Finished-turn source/data failures on in-progress tickets only.

        Looks at the last worker Turn result, not leftover ticket prose. Skips
        in-review/done columns, in-flight turns, and failures already advised
        or reviewed after that result.
        """
        if not self.cfg.get("redirect_on_source_failure", True):
            return []
        tid = ticket.get("id") or ticket.get("ticket_id")
        if not tid:
            return []
        status = ticket.get("status") or ""
        allowed = set(self.cfg.get("redirect_columns") or ["in-progress"])
        if status not in allowed:
            return []
        path, body = self._ticket_body(tid)
        if not body:
            return []
        if self._ticket_turn_active(tid) or prompt_in_flight(body):
            return []
        result = last_worker_result(body)
        if not result:
            return []
        if result_already_handled(body, result):
            return []
        failure = detect_source_failure(result["body"])
        if not failure:
            return []
        sessions = self._sessions_for(bot_id, tid)
        session_id = sessions[0]["session_id"] if sessions else None
        return [Stall(
            bot_id=bot_id,
            kind="source_failure",
            ticket_id=tid,
            session_id=session_id,
            evidence=failure.evidence,
            turn_active=False,
            source=failure.subtype,
            subtype=failure.subtype,
        )]


    def _source_is_cli(self, rec: dict) -> bool:
        return str(rec.get("source") or "").startswith("cli")

    def _source_is_worker(self, rec: dict) -> bool:
        src = str(rec.get("source") or "")
        return src.startswith("opencode") or src.startswith("mcp")

    def _ticket_turn_active(self, ticket_id: str | None) -> bool:
        if not ticket_id:
            return False
        _, body = self._ticket_body(ticket_id)
        if not body:
            return False
        log = body[body.find("# Execution Log"):] if "# Execution Log" in body else body
        headings = list(re.finditer(r"^## (.+)\s*$", log, re.M))
        turn_headings = [m.group(1) for m in headings if m.group(1).startswith("Turn ")]
        if not turn_headings or "progress" not in turn_headings[-1]:
            return False
        last_ts = self._last_activity_ts("", ticket_id, log)
        if last_ts and (time.time() - last_ts) > self.cfg["hung_seconds"]:
            return False
        return True

    def _ticket_mentions(self, ticket_id: str | None, host: str | None, command: str | None) -> bool:
        if not ticket_id:
            return False
        _, body = self._ticket_body(ticket_id)
        if not body:
            return False
        if host and normalize_host(host) and normalize_host(host) in body:
            return True
        if host and host in body:
            return True
        if command:
            snippet = command.strip().splitlines()[0][:80]
            if snippet and snippet in body:
                return True
        return False

    def inspect(self, bot_id: str | None = None) -> list[Stall]:

        bots = enabled_bots(self.s)
        if bot_id:
            if bot_id not in bots:
                raise ValueError(f"unknown or disabled bot {bot_id!r}")
            bots = {bot_id: bots[bot_id]}
        since = time.time() - self.cfg["pending_lookback_seconds"]
        found: list[Stall] = []
        seen_fp: set[str] = set()

        tickets = []
        for col in ACTIVE_COLUMNS:
            tickets.extend(self.store.list(status=col) or [])

        for bid in bots:
            # pending/gate first
            pending = self._pending_host_stalls(bid, since)
            assigned = [t for t in tickets if (t.get("assignee_bot") == bid)]
            # default ticket: most recently updated assigned, else any in-progress
            default_tid = assigned[0]["id"] if assigned else None
            default_sess = (self._sessions_for(bid, default_tid) or self._sessions_for(bid) or [{}])[0]
            for st in pending:
                # Do not pin an unrelated pending decision (e.g. a CLI gate check) onto a live ticket.
                relevant = self._ticket_mentions(default_tid, st.host, st.command)
                worker_src = self._source_is_worker({"source": st.source})
                if (st.host or st.kind == "permission_gate") and not relevant and not worker_src:
                    # CLI / unknown-source host or command blocks do not belong on a live ticket.
                    continue
                if not st.ticket_id:
                    st.ticket_id = default_tid
                if not st.session_id:
                    st.session_id = default_sess.get("session_id")
                st.turn_active = self._ticket_turn_active(st.ticket_id)
                key = self._stall_identity(st)
                if key not in seen_fp:
                    seen_fp.add(key)
                    found.append(st)
            for t in assigned:
                for st in self._ticket_stalls(bid, t, since):
                    key = self._stall_identity(st)
                    if key not in seen_fp:
                        seen_fp.add(key)
                        found.append(st)
                for st in self._source_failure_stalls(bid, t):
                    key = self._stall_identity(st)
                    if key not in seen_fp:
                        seen_fp.add(key)
                        found.append(st)
        return found

    def _intent_for(self, ticket_id: str | None):
        if not ticket_id:
            return extract_intent("", title="")
        _, body = self._ticket_body(ticket_id)
        title = ""
        try:
            rec = self.store.get(ticket_id)
            title = (rec.get("metadata") or {}).get("title") or ""
        except Exception:
            title = ""
        return extract_intent(body, title=title)

    def _assess(self, stall: Stall):
        gate = PermissionGate(stall.bot_id, settings=self.s)
        return assess_pending(
            command=stall.command,
            host=stall.host,
            allowed_hosts=gate.allowed_hosts(),
            safe_suffixes=self.cfg["safe_host_suffixes"],
            intent=self._intent_for(stall.ticket_id),
            extra_packages=self.cfg.get("low_risk_pypi_packages") or [],
        )

    def _decision_from_assessment(self, stall: Stall, assessment, *, defer_resume: bool = False) -> Decision:
        details = {
            "risk": assessment.level,
            "rationale": assessment.rationale,
            "counter_proposal": assessment.counter_proposal,
            "risk_category": assessment.category,
        }
        gate = PermissionGate(stall.bot_id, settings=self.s)
        if stall.kind == "host_approval":
            host = stall.host
            if assessment.approve:
                if host and host_is_allowed(host, gate.allowed_hosts()):
                    action, reason = "resume", assessment.rationale or f"Host {host} is already allowed; resume the worker so it does not wait for a prompt that will never appear."
                    details["gate_approved"] = True
                else:
                    action, reason = "allow_host", assessment.rationale
            else:
                action, reason = "deny", assessment.rationale
        else:
            if assessment.approve:
                if stall.command and gate.is_approved(stall.command):
                    action, reason = "resume", assessment.rationale or "Command is already approved; resume the worker."
                    details["gate_approved"] = True
                else:
                    action, reason = "approve", assessment.rationale
            else:
                action, reason = "reject", assessment.rationale
        d = Decision(action, reason, stall)
        d.details.update(details)
        if defer_resume:
            d.details["defer_resume"] = True
        return d

    def decide(self, stall: Stall) -> Decision:
        if stall.kind == "host_approval":
            host = stall.host
            if not host:
                if stall.turn_active:
                    return Decision("none", "Worker turn is still active; not interrupting an unparseable host stall.", stall)
                return Decision("note", "Host-approval stall without a parseable host; recorded so the ticket does not hang silently.", stall)
            assessment = self._assess(stall)
            if stall.turn_active:
                if assessment.approve:
                    # Silent allow of official/safe hosts (and already-allowed ones) mid-turn; no new prompt.
                    return self._decision_from_assessment(stall, assessment, defer_resume=True)
                return Decision("none", f"Worker turn is still active; not interrupting. Will re-check host {host}.", stall)
            return self._decision_from_assessment(stall, assessment)
        if stall.kind == "permission_gate":
            assessment = self._assess(stall)
            if stall.turn_active:
                if assessment.approve:
                    # May record a low-risk approval so a retry in this turn can pass; do not prompt.
                    return self._decision_from_assessment(stall, assessment, defer_resume=True)
                return Decision("none", "Worker turn is still active; not interrupting a high-risk pending approval.", stall)
            return self._decision_from_assessment(stall, assessment)
        if stall.kind in ("no_progress", "hung_session"):
            return Decision("note", f"{stall.kind.replace('_', ' ')} detected: {stall.evidence}. Recorded on the ticket; Superagent should review rather than invent data or mark Done.", stall)
        if stall.kind == "source_failure":
            if stall.turn_active:
                return Decision("none", "Worker turn is still active; not interrupting a source-failure redirect.", stall)
            title = ""
            body = ""
            if stall.ticket_id:
                _, body = self._ticket_body(stall.ticket_id)
                try:
                    rec = self.store.get(stall.ticket_id)
                    title = (rec.get("metadata") or {}).get("title") or ""
                except Exception:
                    title = ""
            intent = extract_intent(body, title=title)
            failure = SourceFailure(subtype=stall.subtype or stall.source or "missing_source",
                                    evidence=stall.evidence)
            alt = self.advisor(intent, failure)
            details = {
                "intent": intent.summary,
                "suggestion": alt.suggestion,
                "alternative_kind": alt.kind,
                "sources": list(alt.sources or []),
            }
            action = "redirect" if alt.kind == "public_official" else "note"
            d = Decision(action, alt.reason, stall)
            d.details.update(details)
            return d
        return Decision("none", "No action.", stall)

    def _log_ticket(self, ticket_id: str | None, decision: Decision) -> None:
        if not ticket_id or not decision.stall:
            return
        stall = decision.stall
        lines = [
            f"Checked at {now_stamp()} HKT (Asia/Hong_Kong).",
            f"Stall: {stall.kind} on {stall.bot_id}" + (f" for host {stall.host}" if stall.host else "") + (f" ({stall.evidence})" if stall.evidence else ""),
            f"Decision: {decision.action} — {decision.reason}",
        ]
        if decision.details.get("risk"):
            lines.append(f"Risk: {decision.details['risk']}")
        if decision.details.get("rationale"):
            lines.append(f"Rationale: {decision.details['rationale']}")
        if decision.details.get("counter_proposal"):
            lines.append(f"Counter-proposal: {decision.details['counter_proposal']}")
        if decision.details.get("intent"):
            lines.append(f"High-level intent: {decision.details['intent']}")
        if decision.details.get("suggestion"):
            lines.append(f"Suggestion: {decision.details['suggestion']}")
        if decision.details.get("gate_approved") is True:
            lines.append("Gate: approved — the permission gate now allows this exact request.")
        elif decision.details.get("gate_approved") is False or decision.action in ("deny", "reject"):
            lines.append("Gate: not approved — Superagent must not tell the worker this was approved.")
        if decision.applied:
            lines.append("Action: applied by the Superagent worker watchdog.")
        elif decision.skipped:
            lines.append("Action: skipped (already applied recently, or apply disabled).")
        if decision.error:
            lines.append(f"Error: {decision.error}")
        lines.append("Official acceptance-criteria checkboxes left unchecked. Watchdog does not invent figures or mark Done to clear a stall.")
        try:
            self.store.add_log(ticket_id, "Watchdog (superagent)", lines, sync=False)
        except Exception as e:
            decision.error = f"ticket log failed: {type(e).__name__}"

    def _resume_prompt(self, decision: Decision) -> str:
        return briefing_for_decision(decision)

    def _send_resume(self, decision: Decision) -> dict | None:
        stall = decision.stall
        if not stall or not stall.session_id or not stall.bot_id:
            return None
        if stall.turn_active or decision.details.get("defer_resume"):
            return {"status": "skipped", "reason": "turn still active; prompt deferred"}
        if not self.cfg["resume_on_allow"] and decision.action in ("allow_host", "resume", "approve"):
            return {"status": "skipped", "reason": "resume_on_allow is false"}
        # Never brief that a request "has been approved" unless the gate actually allows it.
        if decision.action in ("approve", "allow_host") and not decision.details.get("gate_approved"):
            return {"status": "skipped", "reason": "gate did not approve; not claiming approval"}
        prompt = self._resume_prompt(decision)
        if decision.action in ("deny", "reject", "note") and claims_gate_approval(prompt):
            return {"status": "skipped", "reason": "refusing to send a briefing that claims approval after a denial"}
        if self.sender:
            return self.sender(stall.bot_id, stall.session_id, prompt)
        # Lazy import so unit tests can inject a sender and skip OpenMausBot.
        from scripts.bots.core import BotManager
        mgr = BotManager(settings=self.s, store=self.store)
        return mgr.send_prompt(stall.bot_id, stall.session_id, prompt, wait=False)

    def apply(self, decision: Decision) -> Decision:
        stall = decision.stall
        if not stall or decision.action == "none":
            return decision
        if self._recently_acted(stall, decision.action):
            decision.skipped = True
            decision.details["skipped"] = "recent duplicate"
            # Still do not flood the ticket.
            return decision
        try:
            if decision.action == "allow_host" and stall.host:
                gate = PermissionGate(stall.bot_id, settings=self.s)
                if not host_is_allowed(stall.host, gate.allowed_hosts()):
                    rec = gate.approve_host(stall.host, by="superagent-watchdog")
                    decision.details["approved_host"] = rec
                if host_is_allowed(stall.host, gate.allowed_hosts()):
                    decision.details["gate_approved"] = True
                else:
                    decision.details["gate_approved"] = False
                    decision.error = decision.error or "gate did not allow the host after approve-host"
                if self.cfg["resume_on_allow"] and decision.details.get("gate_approved"):
                    decision.details["resume"] = self._send_resume(decision)
            elif decision.action == "approve" and stall.command:
                gate = PermissionGate(stall.bot_id, settings=self.s)
                if not gate.is_approved(stall.command):
                    rec = gate.approve(stall.command, by="superagent-watchdog")
                    decision.details["approved_command"] = rec
                checked = gate.check(stall.command)
                decision.details["gate_decision"] = checked.decision
                if checked.decision == "allow":
                    decision.details["gate_approved"] = True
                else:
                    decision.details["gate_approved"] = False
                    decision.error = (
                        f"gate did not allow after approve: {checked.decision} ({checked.reason})"
                    )
                if decision.details.get("gate_approved"):
                    decision.details["resume"] = self._send_resume(decision)
            elif decision.action == "resume":
                # Already allowed by the gate — safe to say so.
                decision.details.setdefault("gate_approved", True)
                decision.details["resume"] = self._send_resume(decision)
            elif decision.action in ("deny", "reject"):
                decision.details["gate_approved"] = False
                decision.details["resume"] = self._send_resume(decision)
            elif decision.action == "redirect":
                decision.details["resume"] = self._send_resume(decision)
            # note: log only. Never auto-buy, log in, tick AC, or mark Done.
            # Do not mark applied-success for approve if the gate refused.
            if decision.action == "approve" and not decision.details.get("gate_approved"):
                decision.applied = False
            else:
                decision.applied = True
        except Exception as e:
            decision.error = f"{type(e).__name__}: {e}"
            decision.applied = False
            decision.details["gate_approved"] = False
        self._log_ticket(stall.ticket_id, decision)
        self._record_action(decision)
        return decision

    def run_once(self, apply: bool | None = None, bot_id: str | None = None) -> dict[str, Any]:
        if not self.cfg["enabled"]:
            return {"enabled": False, "bots": list(enabled_bots(self.s)), "stalls": [], "decisions": []}
        apply = self.cfg["auto_apply"] if apply is None else apply
        bots = list(enabled_bots(self.s) if not bot_id else [bot_id])
        stalls = self.inspect(bot_id)
        decisions = [self.decide(s) for s in stalls]
        if apply:
            decisions = [self.apply(d) if d.action != "none" else d for d in decisions]
        # record last_run even when healthy
        state = self._load_state()
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        state["last_result"] = {
            "bots": bots,
            "stalls": len(stalls),
            "actions": [d.action for d in decisions],
        }
        self._save_state(state)
        return {
            "enabled": True,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "interval_seconds": self.cfg["interval_seconds"],
            "safe_host_suffixes": self.cfg["safe_host_suffixes"],
            "bots": bots,
            "apply": apply,
            "stalls": [asdict(s) for s in stalls],
            "decisions": [d.to_dict() for d in decisions],
        }

    def run_loop(self, interval: int | None = None) -> None:
        interval = int(interval or self.cfg["interval_seconds"] or DEFAULT_INTERVAL)
        interval = max(30, min(interval, 120))
        while True:
            try:
                result = self.run_once()
                _log_loop(result)
            except Exception as e:
                sys.stderr.write(f"{datetime.now().isoformat(timespec='seconds')} watchdog error: {type(e).__name__}: {e}\n")
                sys.stderr.flush()
            time.sleep(interval)


def _log_loop(result: dict) -> None:
    n = len(result.get("stalls") or [])
    actions = [d.get("action") for d in (result.get("decisions") or [])]
    line = (
        f"{result.get('checked_at')} bots={result.get('bots')} stalls={n} "
        f"actions={actions or ['none']} apply={result.get('apply')}\n"
    )
    sys.stdout.write(line)
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Superagent worker watchdog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    once = sub.add_parser("once")
    once.add_argument("--bot")
    once.add_argument("--no-apply", action="store_true")
    loop = sub.add_parser("loop")
    loop.add_argument("--interval", type=int)
    args = ap.parse_args(argv)
    wd = WorkerWatchdog()
    if args.cmd == "once":
        result = wd.run_once(apply=not args.no_apply, bot_id=args.bot)
        print(json.dumps(result, indent=2))
        return 0
    wd.run_loop(interval=args.interval)
    return 0


if __name__ == "__main__":
    if os.environ.get("GIT_CONFIG_COUNT") and not os.environ.get("GIT_CONFIG_KEY_0"):
        os.environ.pop("GIT_CONFIG_COUNT", None)
        os.environ.pop("GIT_CONFIG_VALUE_0", None)
    raise SystemExit(main())
