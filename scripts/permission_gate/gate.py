"""Permission gate for bot command execution (the blueprint's tool.execute.before gate).

Used by:
  * bot_run_command / bot_run_tests in mcp_servers/bots_mcp.py (superagent-initiated commands)
  * the opencode plugin `.opencode/plugins/kanban-permission-gate.js` installed in each bot
    worktree, which OpenCode (OpenMausBot's coding engine) calls on `tool.execute.before`
    and `shell create.before` (bot-initiated commands)

Decisions: allow | needs_approval | deny.  Order: deny > needs_approval > allow > default.
Human approvals for a specific command are stored in .state/approvals.json
(`python -m scripts.permission_gate.gate approve --bot bot-001 -- "<cmd>"`).
Host-level approvals (for unknown-host network calls) are stored in
.state/approved-hosts.json (`bin/gate approve-host --bot bot-001 --host example.com`).
Pending needs_approval decisions are appended to .state/pending-approvals.jsonl.
OpenMausBot is non-interactive: needs_approval fails the tool call immediately
with a resolution hint; it does not wait for a prompt that will never appear.

CLI (exit code 0 allow, 2 deny, 3 needs approval; JSON on stdout):
  python -m scripts.permission_gate.gate check --bot bot-001 [--cwd DIR] -- <command...>
  python -m scripts.permission_gate.gate check-path --bot bot-001 --path FILE [--write]
  python -m scripts.permission_gate.gate approve-host --bot bot-001 --host HOST
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.config import load_settings  # noqa: E402

RANK = {"allow": 0, "needs_approval": 1, "deny": 2}
SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
URL_RE = re.compile(r"\b(?:https?|ftp|wss?)://[^\s'\"|;&)]+", re.I)
UNKNOWN_HOST_RE = re.compile(r"unknown host ([A-Za-z0-9.-]+)")
# Single-label names and bare public suffixes must not be approvable (would be too broad).
TOO_BROAD_HOSTS = frozenset({
    "localhost", "com", "net", "org", "edu", "gov", "mil", "int", "io", "co",
    "uk", "us", "hk", "cn", "au", "de", "fr", "jp", "info", "biz", "xyz", "app",
})


def normalize_host(value: str | None) -> str:
    """Extract a lowercase hostname from a URL or host string."""
    host = (value or "").strip().lower()
    if "://" in host:
        host = (urlparse(host).hostname or "")
    host = host.split("/")[0].split(":")[0].strip().strip(".")
    if host.startswith("*."):
        host = host[2:]
    return host


def host_is_allowed(host: str, allowed: set[str]) -> bool:
    """True if host equals an allowed entry or is a subdomain of one."""
    host = (host or "").lower().strip(".")
    if not host:
        return False
    return any(host == h or (h and host.endswith("." + h)) for h in allowed)


def host_is_approvable(host: str) -> bool:
    """Reject empty, wildcard, or too-broad names (e.g. 'com', 'hk') that would open the internet."""
    host = (host or "").lower().strip(".")
    if not host or "*" in host or "/" in host or host in TOO_BROAD_HOSTS:
        return False
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return False
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2 or any(not p for p in labels):
        return False
    return True


@dataclass
class Decision:
    decision: str
    reason: str
    rule: str | None = None
    command: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict:
        return asdict(self)


def split_segments(cmd: str) -> list[str]:
    """Split a shell command on && || ; | and newlines, respecting quotes (best effort)."""
    segs, buf, q, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            buf.append(c)
            if c == q:
                q = None
            elif c == "\\" and q == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1]); i += 1
        elif c in "'\"":
            q = c; buf.append(c)
        elif c in ";\n|&":
            if c in "|&" and i + 1 < len(cmd) and cmd[i + 1] == c:
                i += 1
            elif c == "&" and not (i + 1 < len(cmd) and cmd[i + 1] == "&"):
                # lone & (background) or redirection like 2>&1
                if buf and buf[-1] in "<>":
                    buf.append(c); i += 1; continue
            segs.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    segs.append("".join(buf))
    out = []
    for s in segs:
        s = s.strip()
        s = re.sub(r"^(\(|\{)\s*", "", s)
        # drop leading VAR=value assignments
        while re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+", s):
            s = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+", "", s, count=1)
        if s:
            out.append(s)
    return out


class PermissionGate:
    def __init__(self, bot_id: str, workspace: str | Path | None = None, policy: dict | None = None, settings=None):
        self.s = settings or load_settings()
        self.bot_id = bot_id
        cfg = self.s.sandbox_config(bot_id) if policy is None else {"permission_gate": policy}
        self.policy = cfg.get("permission_gate", {}) or {}
        ws = workspace or (cfg.get("workspace") or {}).get("path") or (self.s.registry().get(bot_id) or {}).get("workspace")
        self.workspace = Path(ws).resolve() if ws else None

    # ----- approvals -----
    @property
    def approvals_path(self) -> Path:
        return self.s.state_dir / "approvals.json"

    def _key(self, cmd: str) -> str:
        return hashlib.sha256(f"{self.bot_id}\0{cmd.strip()}".encode()).hexdigest()[:24]

    def approve(self, cmd: str, by: str = "human") -> dict:
        data = json.loads(self.approvals_path.read_text()) if self.approvals_path.exists() else {}
        data[self._key(cmd)] = {"bot": self.bot_id, "command": cmd.strip(), "by": by,
                                "at": datetime.now().isoformat(timespec="seconds")}
        self.approvals_path.write_text(json.dumps(data, indent=2))
        return data[self._key(cmd)]

    def is_approved(self, cmd: str) -> bool:
        if not self.approvals_path.exists():
            return False
        return self._key(cmd) in json.loads(self.approvals_path.read_text())

    @property
    def approved_hosts_path(self) -> Path:
        return self.s.state_dir / "approved-hosts.json"

    @property
    def pending_path(self) -> Path:
        return self.s.state_dir / "pending-approvals.jsonl"

    def runtime_approved_hosts(self) -> set[str]:
        path = self.approved_hosts_path
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return set()
        bot = data.get(self.bot_id) or {}
        if not isinstance(bot, dict):
            return set()
        return {normalize_host(h) for h in bot.keys() if normalize_host(h)}

    def allowed_hosts(self) -> set[str]:
        net = self.policy.get("network") or {}
        hosts = {normalize_host(h) for h in (net.get("allowed_hosts") or []) if normalize_host(h)}
        hosts |= self.runtime_approved_hosts()
        return hosts

    def approve_host(self, host: str, by: str = "human") -> dict:
        host = normalize_host(host)
        if not host_is_approvable(host):
            raise ValueError(f"host {host!r} is empty or too broad to approve")
        path = self.approved_hosts_path
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        bot = data.setdefault(self.bot_id, {})
        rec = {"host": host, "by": by, "at": datetime.now().isoformat(timespec="seconds")}
        bot[host] = rec
        path.write_text(json.dumps(data, indent=2))
        return rec

    def record_pending(self, d: Decision, source: str | None = None) -> None:
        """Append a needs_approval decision so operators can see blocked hosts/commands."""
        if d.decision != "needs_approval":
            return
        host = None
        if d.reason:
            m = UNKNOWN_HOST_RE.search(d.reason)
            if m:
                host = normalize_host(m.group(1))
        if d.rule == "network" and not host and d.command:
            urls = URL_RE.findall(d.command)
            if urls:
                host = normalize_host(urlparse(urls[0]).hostname or "")
        resolution = (
            f"bin/gate approve-host --bot {self.bot_id} --host {host}"
            if host else
            f"bin/gate approve --bot {self.bot_id} -- {d.command or '<command>'}"
        )
        rec = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "bot": self.bot_id,
            "host": host,
            "rule": d.rule,
            "reason": d.reason,
            "command": d.command,
            "resolution": resolution,
            "source": source,
        }
        with open(self.pending_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _unknown_host_decision(self, host: str) -> Decision:
        net = self.policy.get("network") or {}
        decision = net.get("unknown_host", "needs_approval")
        reason = (
            f"network call to unknown host {host}. "
            "This environment is non-interactive: no approval prompt will appear. "
            "Do not wait for a prompt. Report the blocked host in your reply and stop this network step. "
            f"A human can allow the host with: bin/gate approve-host --bot {self.bot_id} --host {host}"
        )
        return Decision(decision, reason, "network")

    # ----- evaluation -----
    def _match(self, kind: str, text: str) -> str | None:
        for pat in self.policy.get(kind, []) or []:
            try:
                if re.search(pat, text):
                    return pat
            except re.error:
                continue
        return None

    def _roots(self) -> list[str]:
        roots = []
        for r in (self.policy.get("paths") or {}).get("allowed_roots", []) or []:
            if r == "WORKSPACE":
                if self.workspace:
                    roots.append(str(self.workspace))
            else:
                roots.append(r)
        return roots

    def _path_ok(self, p: str, cwd: Path | None) -> bool:
        path = Path(p)
        if not path.is_absolute():
            path = (cwd or self.workspace or Path.cwd()) / path
        path = Path(os.path.normpath(str(path)))
        return any(str(path) == r or str(path).startswith(r.rstrip("/") + "/") for r in self._roots())

    def check(self, cmd: str, cwd: str | Path | None = None) -> Decision:
        cmd = (cmd or "").strip()
        if not cmd:
            return Decision("deny", "empty command", command=cmd)
        cwd_path = Path(cwd).resolve() if cwd else self.workspace
        # working directory restriction
        if self.workspace and cwd_path and not (cwd_path == self.workspace or self.workspace in cwd_path.parents):
            return self._final(Decision("deny", f"working directory {cwd_path} is outside the bot workspace", "cwd", cmd))
        # whole-command deny rules first (catch pipes like curl | sh)
        rule = self._match("deny", cmd)
        if rule:
            return self._final(Decision("deny", "matches deny rule", rule, cmd))
        worst = Decision("allow", "allowed", None, cmd)
        for seg in split_segments(cmd):
            d = self._check_segment(seg, cwd_path)
            if RANK[d.decision] > RANK[worst.decision]:
                worst = d
        worst.command = cmd
        return self._final(worst)

    def _check_segment(self, seg: str, cwd: Path | None) -> Decision:
        rule = self._match("deny", seg)
        if rule:
            return Decision("deny", f"segment {seg!r} matches deny rule", rule)
        rule = self._match("needs_approval", seg)
        if rule:
            return Decision("needs_approval", f"segment {seg!r} needs approval", rule)
        allowed_hosts = self.allowed_hosts()
        for url in URL_RE.findall(seg):
            host = normalize_host(urlparse(url).hostname or "")
            if host and not host_is_allowed(host, allowed_hosts):
                return self._unknown_host_decision(host)
        paths_cfg = self.policy.get("paths") or {}
        if paths_cfg.get("allowed_roots"):
            try:
                tokens = shlex.split(seg, posix=True)
            except ValueError:
                tokens = seg.split()
            for tok in tokens[1:]:
                cand = tok.split("=", 1)[-1] if tok.startswith("--") else tok
                if cand.startswith("/") or cand.startswith("~") or cand.startswith(".."):
                    cand = os.path.expanduser(cand)
                    if not self._path_ok(cand, cwd):
                        return Decision(paths_cfg.get("outside_roots", "needs_approval"),
                                        f"path {tok} is outside the allowed roots", "paths")
        rule = self._match("allow", seg)
        if rule:
            return Decision("allow", "matches allow rule", rule)
        default = self.policy.get("default", "needs_approval")
        return Decision(default, f"no rule matched (default {default})", "default")

    def check_path(self, path: str, write: bool = False) -> Decision:
        if not self.workspace:
            return Decision("deny", "bot has no workspace configured")
        p = Path(path)
        full = Path(os.path.normpath(str(p if p.is_absolute() else self.workspace / p)))
        if not (full == self.workspace or self.workspace in full.parents):
            return self._final(Decision("deny", f"{path} is outside the bot workspace", "paths", path))
        rel = full.relative_to(self.workspace)
        if rel.parts and rel.parts[0] == ".git" or (full.name == ".git"):
            return self._final(Decision("deny", "modifying .git/ manually is not allowed", "git", path))
        if write and rel.parts and rel.parts[0] == ".opencode" and "plugins" in rel.parts:
            return self._final(Decision("deny", "the permission gate plugin cannot be modified by the bot", "gate", path))
        return Decision("allow", "inside workspace", None, path)

    def _final(self, d: Decision) -> Decision:
        if d.decision == "needs_approval" and d.command and self.is_approved(d.command):
            return Decision("allow", f"approved by a human ({d.reason})", d.rule, d.command)
        return d

    def audit(self, d: Decision, source: str) -> None:
        self.record_pending(d, source=source)
        with open(self.s.state_dir / "gate-audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "bot": self.bot_id,
                                "source": source, **d.to_dict()}) + "\n")


def scrubbed_env(extra_path: str | None = None) -> dict:
    """Environment for bot commands: no secrets."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # keep bytecode out of bot commits
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    return env


def redact(text: str) -> str:
    for k, v in os.environ.items():
        if SECRET_ENV_RE.search(k) and v and len(v) >= 8:
            text = text.replace(v, f"[REDACTED:{k}]")
    text = re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[REDACTED]", text)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kanban bots permission gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--bot", required=True); c.add_argument("--cwd")
    c.add_argument("--source", default="cli"); c.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("check-path"); p.add_argument("--bot", required=True); p.add_argument("--path", required=True)
    p.add_argument("--write", action="store_true"); p.add_argument("--source", default="cli")
    a = sub.add_parser("approve"); a.add_argument("--bot", required=True); a.add_argument("command", nargs=argparse.REMAINDER)
    ah = sub.add_parser("approve-host"); ah.add_argument("--bot", required=True); ah.add_argument("--host", required=True)
    ah.add_argument("--by", default="human")
    args = ap.parse_args(argv)
    if args.cmd == "approve":
        parts = args.command[1:] if args.command[:1] == ["--"] else args.command
        print(json.dumps(PermissionGate(args.bot).approve(" ".join(parts).strip())))
        return 0
    if args.cmd == "approve-host":
        try:
            print(json.dumps(PermissionGate(args.bot).approve_host(args.host, by=args.by)))
            return 0
        except ValueError as e:
            print(json.dumps({"error": str(e)}))
            return 2
    gate = PermissionGate(args.bot)
    if args.cmd == "check":
        parts = args.command[1:] if args.command[:1] == ["--"] else args.command
        d = gate.check(" ".join(parts), args.cwd)
    else:
        d = gate.check_path(args.path, args.write)
    gate.audit(d, args.source)
    print(json.dumps(d.to_dict()))
    return {"allow": 0, "deny": 2, "needs_approval": 3}[d.decision]


if __name__ == "__main__":
    raise SystemExit(main())
