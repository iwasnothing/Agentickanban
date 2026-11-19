"""Kanban bot operations: the implementation behind the bot.* MCP tools.

Each Kanban bot (bots/registry.yaml) = one OpenMausBot bot + one git worktree of the
project repo on branch bots/<bot-id>. A session = one OpenMausBot task (conversation)
whose working folder is the worktree. Turn bookkeeping lives in .state/sessions.json.
"""
from __future__ import annotations

import json
import time
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from scripts.config import Settings, load_settings
from scripts.omb.client import OpenMausBot, format_activity
from scripts.permission_gate.gate import PermissionGate, UNKNOWN_HOST_RE, normalize_host, redact, scrubbed_env
from scripts.ticket_sync import md_utils as md
from scripts.ticket_sync.store import TicketStore

MAX_FILE_BYTES = 512_000
MAX_OUTPUT = 20_000


class BotError(ValueError):
    pass


def _git(ws: Path, *args: str, env: dict | None = None, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise BotError(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


class BotManager:
    def __init__(self, settings: Settings | None = None, store: TicketStore | None = None, omb: OpenMausBot | None = None):
        self.s = settings or load_settings()
        self.store = store or TicketStore(self.s)
        o = self.s.omb
        self.omb = omb or OpenMausBot(o.get("url", "http://127.0.0.1:8799"), o.get("cli", "openmausbot"))
        self.sessions_path = self.s.state_dir / "sessions.json"

    # ---------- registry / sessions ----------
    def _bot(self, bot_id: str) -> dict:
        reg = self.s.registry()
        if bot_id not in reg:
            raise BotError(f"unknown bot {bot_id!r}; registered: {sorted(reg)}")
        info = dict(reg[bot_id])
        info.setdefault("workspace", str(self.s.workspaces_root / bot_id))
        info.setdefault("branch", f"bots/{bot_id}")
        return info

    def _ws(self, bot_id: str) -> Path:
        ws = Path(self._bot(bot_id)["workspace"])
        if not (ws / ".git").exists():
            self.ensure_worktree(bot_id)
        return ws

    def _sessions(self) -> dict:
        return json.loads(self.sessions_path.read_text()) if self.sessions_path.exists() else {}

    def _save_sessions(self, data: dict) -> None:
        tmp = self.sessions_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.sessions_path)

    def _session(self, bot_id: str, session_id: str) -> dict:
        sess = self._sessions().get(session_id)
        if not sess or sess["bot_id"] != bot_id:
            raise BotError(f"no session {session_id!r} for bot {bot_id!r}")
        return sess

    def _update_session(self, session_id: str, **fields) -> dict:
        data = self._sessions()
        data[session_id].update(fields)
        self._save_sessions(data)
        return data[session_id]

    def ensure_worktree(self, bot_id: str) -> str:
        info = self._bot(bot_id)
        script = self.s.root / "scripts" / "workspace" / "create_worktree.sh"
        env = {**os.environ, "AIBOTS_PROJECT_REPO": str(self.s.project_repo),
               "AIBOTS_WORKSPACES_ROOT": str(Path(info["workspace"]).parent)}
        p = subprocess.run([str(script), bot_id], capture_output=True, text=True, env=env)
        if p.returncode != 0:
            raise BotError(f"create_worktree failed: {p.stderr.strip()[:500]}")
        return p.stdout.strip().splitlines()[-1]

    # ---------- bot.list / bot.get_config ----------
    def list(self) -> list[dict]:
        live = {}
        try:
            live = {b["id"]: b for b in self.omb.bots()}
            reachable = True
        except Exception:
            reachable = False
        out = []
        for bot_id, info in self.s.registry().items():
            omb = live.get(info.get("openmausbot_bot_id"), {})
            ws = Path(info.get("workspace", self.s.workspaces_root / bot_id))
            out.append({
                "bot_id": bot_id, "name": info.get("name"), "enabled": info.get("enabled", True),
                "openmausbot_bot": {"id": info.get("openmausbot_bot_id"), "name": omb.get("name") or info.get("openmausbot_bot_name"),
                                    "activity": omb.get("activity") if reachable else "openmausbot-unreachable",
                                    "busy": omb.get("busy"), "model": omb.get("modelSelection")},
                "workspace": str(ws), "branch": info.get("branch", f"bots/{bot_id}"), "workspace_ready": (ws / ".git").exists(),
                "open_tickets": [t["id"] for c in ("to-do", "planning", "in-progress", "in-review")
                                 for t in self.store.list(status=c) if t.get("assignee_bot") == bot_id],
            })
        return out

    def get_config(self, bot_id: str) -> dict:
        self._bot(bot_id)
        d = self.s.bots_dir / bot_id
        read = lambda n: (d / n).read_text(encoding="utf-8") if (d / n).exists() else None  # noqa: E731
        mcp = read("mcp.json")
        return {"bot_id": bot_id, "AGENTS.md": read("AGENTS.md"), "SKILL.md": read("SKILL.md"),
                "mcp": json.loads(mcp) if mcp else None, "sandbox": self.s.sandbox_config(bot_id),
                "registry": self._bot(bot_id)}

    # ---------- sessions & prompts ----------
    def start_session(self, bot_id: str, ticket_id: str) -> dict:
        info = self._bot(bot_id)
        ticket = self.store.get(ticket_id)
        ws = Path(self.ensure_worktree(bot_id))
        # stage ticket + attachments into the workspace (excluded from commits)
        tdir = ws / ".ticket"
        (tdir / "attachments").mkdir(parents=True, exist_ok=True)
        (tdir / "TICKET.md").write_text(ticket["markdown"], encoding="utf-8")
        copied = []
        for a in self.store.attachment_paths(ticket_id):
            shutil.copy2(a, tdir / "attachments" / a.name)
            copied.append(f".ticket/attachments/{a.name}")
        task = self.omb.create_task(info["openmausbot_bot_id"], f"[{ticket_id}] {ticket['metadata']['title']}", str(ws))
        session_id = task["threadId"]
        data = self._sessions()
        data[session_id] = {"session_id": session_id, "bot_id": bot_id, "ticket_id": ticket_id,
                            "openmausbot_bot_id": info["openmausbot_bot_id"], "workspace": str(ws),
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "turn": md.count_turns(ticket["markdown"].split("---", 2)[-1], bot_id),
                            "turn_base": _git(ws, "rev-parse", "HEAD").strip(), "commits": [], "persona_sent": False}
        self._save_sessions(data)
        self.store.add_log(ticket_id, f"Session started ({bot_id})",
                           [f"Session {session_id} on branch {info['branch']} at {datetime.now():%Y-%m-%d %H:%M}",
                            f"Attachments copied: {', '.join(copied) or 'none'}"], sync=False)
        return {"session_id": session_id, "bot_id": bot_id, "ticket_id": ticket_id, "workspace": str(ws),
                "openmausbot_task_cwd": task.get("cwd"), "attachments": copied}

    def _persona_preamble(self, bot_id: str) -> str:
        cfg = self.get_config(bot_id)
        return ("You are operating as Kanban bot `{b}`. Your persona and constraints (AGENTS.md):\n\n{a}\n\n"
                "Your skills (SKILL.md):\n\n{k}\n\nThe full ticket is in `.ticket/TICKET.md`.\n\n---\n\n"
                ).format(b=bot_id, a=cfg["AGENTS.md"] or "", k=cfg["SKILL.md"] or "")

    def send_prompt(self, bot_id: str, session_id: str, prompt: str, wait: bool = True, timeout_s: int | None = None) -> dict:
        sess = self._session(bot_id, session_id)
        ws = Path(sess["workspace"])
        turn = sess["turn"] + 1
        if not sess.get("persona_sent"):
            prompt = self._persona_preamble(bot_id) + prompt
        base = _git(ws, "rev-parse", "HEAD").strip()
        self._update_session(session_id, turn=turn, turn_base=base, persona_sent=True)
        send_id = f"kb-{session_id[:8]}-t{turn}-{uuid.uuid4().hex[:8]}"
        receipt = self.omb.send(sess["openmausbot_bot_id"], session_id, prompt, send_id=send_id)
        self.store.add_log(sess["ticket_id"], f"Turn {turn} ({bot_id})",
                           [f"Prompt sent at {md.now_stamp()} (session {session_id})",
                            "Prompt: " + prompt.strip().splitlines()[-1][:200] if prompt.strip() else ""], sync=False)
        out = {"session_id": session_id, "turn": turn, "receipt": receipt}
        if wait:
            omb_id = sess["openmausbot_bot_id"]
            cfg = self.s.omb
            slice_s = max(1, int(cfg.get("progress_log_seconds", 60)))
            base_s = int(timeout_s if timeout_s is not None else cfg.get("default_wait_seconds", 1800))
            max_s = max(base_s, int(cfg.get("max_wait_seconds", 3600)))
            started = time.monotonic()
            last_fp = None
            res: dict = {"status": "timed-out", "exit_code": 4}
            while True:
                remaining = max_s - (time.monotonic() - started)
                if remaining <= 0:
                    break
                wait_for = max(1, int(min(slice_s, remaining)))
                slice_started = time.monotonic()
                try:
                    res = self.omb.wait(omb_id, session_id, timeout_s=wait_for)
                except Exception as e:
                    res = {"status": "failed", "exit_code": 1, "stderr": str(e)}
                    break
                status = res.get("status")
                if status in ("done", "failed", "needs-user"):
                    break
                # If wait() returned early (tests or a CLI glitch), do not spin the log loop.
                leftover = wait_for - (time.monotonic() - slice_started)
                if leftover > 0.05:
                    time.sleep(min(leftover, max(0, max_s - (time.monotonic() - started))))
                elapsed = time.monotonic() - started
                snap = self._activity_snapshot(omb_id, session_id)
                activity = format_activity(snap) if snap else "turn still running"
                fp = snap.get("fingerprint") or activity
                if fp == last_fp:
                    line = f"Still working at {md.now_stamp()} ({int(elapsed)}s elapsed): no new tool activity; {activity}."
                else:
                    line = f"Still working at {md.now_stamp()} ({int(elapsed)}s elapsed): {activity}."
                self.store.add_log(sess["ticket_id"], f"Turn {turn} progress ({bot_id})",
                                   [line], sync=False)
                last_fp = fp
                # After the default wait, stop if the turn has gone idle (no longer active).
                if elapsed >= base_s and snap and not snap.get("active"):
                    break
                if time.monotonic() - started >= max_s:
                    break
            out["status"] = res.get("status") or "timed-out"
            if res.get("stderr"):
                out["detail"] = redact(res["stderr"])[-1000:]
            snap = self._activity_snapshot(omb_id, session_id)
            try:
                out.update(self.omb.last_reply(omb_id, session_id))
            except Exception as e:
                out["reply_error"] = str(e)
            if snap:
                out["activity"] = snap.get("summary") or format_activity(snap)
                if snap.get("reply") and not (out.get("reply") or "").strip():
                    out["reply"] = snap["reply"]
            reply = (out.get("reply") or "").strip().replace("\n", " ")
            elapsed = time.monotonic() - started
            result_lines = [f"Status: {out['status']} at {md.now_stamp()} ({int(elapsed)}s elapsed)"]
            if reply:
                result_lines.append(f"Bot reply: {reply[:300]}")
            elif snap and (snap.get("tool_total") or snap.get("active") or snap.get("summary")):
                result_lines.append(f"In-progress activity: {out.get('activity') or format_activity(snap)}.")
                if out["status"] == "timed-out":
                    result_lines.append("No finished text reply yet; the turn was still running when the wait ended.")
            else:
                result_lines.append("No tool activity or text reply recorded.")
            self.store.add_log(sess["ticket_id"], f"Turn {turn} result ({bot_id})", result_lines, sync=False)
        else:
            out["status"] = "dispatched"
        return out

    def _activity_snapshot(self, omb_id: str, session_id: str) -> dict:
        fn = getattr(self.omb, "activity_snapshot", None)
        if not callable(fn):
            return {}
        try:
            return fn(omb_id, session_id) or {}
        except Exception:
            return {}

    def get_session_status(self, bot_id: str, session_id: str) -> dict:
        sess = self._session(bot_id, session_id)
        return {**sess, **self.omb.last_reply(sess["openmausbot_bot_id"], session_id)}

    # ---------- workspace ----------
    def get_workspace_tree(self, bot_id: str, max_entries: int = 500) -> dict:
        ws = self._ws(bot_id)
        tracked = _git(ws, "ls-files").splitlines()
        untracked = _git(ws, "ls-files", "--others", "--exclude-standard").splitlines()
        status = _git(ws, "status", "--porcelain").splitlines()
        files = sorted(set(tracked) | set(untracked))
        return {"bot_id": bot_id, "workspace": str(ws), "branch": _git(ws, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                "head": _git(ws, "rev-parse", "--short", "HEAD").strip(), "files": files[:max_entries],
                "truncated": len(files) > max_entries, "status": status,
                "ticket_files": sorted(str(p.relative_to(ws)) for p in (ws / ".ticket").rglob("*") if p.is_file())
                if (ws / ".ticket").exists() else []}

    def _gate(self, bot_id: str) -> PermissionGate:
        return PermissionGate(bot_id, workspace=self._ws(bot_id), settings=self.s)

    def read_file(self, bot_id: str, path: str) -> dict:
        gate = self._gate(bot_id)
        d = gate.check_path(path)
        if not d.allowed:
            raise BotError(d.reason)
        full = gate.workspace / path if not Path(path).is_absolute() else Path(path)
        if not full.is_file():
            raise BotError(f"no such file: {path}")
        data = full.read_bytes()[:MAX_FILE_BYTES]
        return {"path": str(full.relative_to(gate.workspace)), "content": data.decode("utf-8", errors="replace"),
                "truncated": full.stat().st_size > MAX_FILE_BYTES}

    def write_file(self, bot_id: str, path: str, content: str) -> dict:
        gate = self._gate(bot_id)
        d = gate.check_path(path, write=True)
        gate.audit(d, "mcp:bot_write_file")
        if not d.allowed:
            raise BotError(d.reason)
        full = gate.workspace / path if not Path(path).is_absolute() else Path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"path": str(full.relative_to(gate.workspace)), "bytes": len(content.encode())}

    def get_last_turn_diff(self, bot_id: str, session_id: str) -> dict:
        """Diff from the start of the current turn to the worktree (including new files).
        After commit_turn, returns the last committed turn's diff."""
        sess = self._session(bot_id, session_id)
        ws = Path(sess["workspace"])
        base = sess["turn_base"]
        head = _git(ws, "rev-parse", "HEAD").strip()
        dirty = bool(_git(ws, "status", "--porcelain").strip())
        if not dirty and head == base and sess.get("commits"):
            last = sess["commits"][-1]
            base, ref, mode = last["base"], last["commit"], "committed"
        else:
            ref, mode = None, "working-tree"
        # temp index so untracked files show up without touching the real index
        idx = self.s.state_dir / f"diff-index-{bot_id}"
        env = {**os.environ, "GIT_INDEX_FILE": str(idx)}
        try:
            if ref:
                diff = _git(ws, "diff", "--stat", "--patch", base, ref)
                stat = _git(ws, "diff", "--numstat", base, ref)
            else:
                _git(ws, "read-tree", "HEAD", env=env)
                _git(ws, "add", "-A", env=env)
                diff = _git(ws, "diff", "--cached", "--stat", "--patch", base, env=env)
                stat = _git(ws, "diff", "--cached", "--numstat", base, env=env)
        finally:
            idx.unlink(missing_ok=True)
        files = [l.split("\t")[-1] for l in stat.splitlines() if l.strip()]
        return {"session_id": session_id, "turn": sess["turn"], "mode": mode, "base": base[:12],
                "files_changed": files, "diff": redact(diff)[:60_000], "truncated": len(diff) > 60_000}

    def commit_turn(self, bot_id: str, session_id: str, message: str | None = None) -> dict:
        sess = self._session(bot_id, session_id)
        ws = Path(sess["workspace"])
        base = sess["turn_base"]
        script = self.s.root / "scripts" / "workspace" / "commit_push_turn.sh"
        args = [str(script), str(ws), bot_id, sess["ticket_id"], str(max(sess["turn"], 1))]
        if message:
            args.append(message)
        p = subprocess.run(args, capture_output=True, text=True)
        if p.returncode != 0:
            raise BotError(f"commit failed: {p.stderr.strip()[:500]}")
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            res = {"raw": p.stdout.strip()}
        if res.get("committed"):
            commits = sess.get("commits", []) + [{"turn": sess["turn"], "commit": _git(ws, "rev-parse", "HEAD").strip(),
                                                  "base": base, "message": res.get("message")}]
            self._update_session(session_id, commits=commits, turn_base=_git(ws, "rev-parse", "HEAD").strip())
            self.store.add_log(sess["ticket_id"], f"Turn {sess['turn']} commit ({bot_id})",
                               [f"Commit {res['head']} on bots/{bot_id}: {res.get('message')}", f"Push: {res.get('push')}"],
                               sync=False)
        return res

    def _run(self, bot_id: str, cmd: str, source: str, timeout: int | None = None) -> dict:
        gate = self._gate(bot_id)
        d = gate.check(cmd, gate.workspace)
        gate.audit(d, source)
        if not d.allowed:
            hint = None
            if d.decision == "needs_approval":
                host = None
                if d.reason:
                    m = UNKNOWN_HOST_RE.search(d.reason)
                    if m:
                        host = normalize_host(m.group(1))
                if host:
                    hint = (
                        "This environment is non-interactive (no approval prompt will appear). "
                        f"A human can allow the host with: bin/gate approve-host --bot {bot_id} --host {host} "
                        f"(or approve this exact command with: python -m scripts.permission_gate.gate approve --bot {bot_id} -- '{cmd}')"
                    )
                else:
                    hint = (
                        "This environment is non-interactive (no approval prompt will appear). "
                        f"A human can approve with: python -m scripts.permission_gate.gate approve --bot {bot_id} -- '{cmd}'"
                    )
            return {"status": "blocked" if d.decision == "deny" else "needs_approval", "decision": d.to_dict(),
                    "hint": hint}
        cfg = self.s.sandbox_config(bot_id)
        timeout = timeout or int(cfg.get("command_timeout_seconds", 300))
        try:
            p = subprocess.run(["bash", "-c", cmd], cwd=gate.workspace, capture_output=True, text=True,
                               timeout=timeout, env=scrubbed_env())
            return {"status": "ok" if p.returncode == 0 else "failed", "exit_code": p.returncode,
                    "stdout": redact(p.stdout)[-MAX_OUTPUT:], "stderr": redact(p.stderr)[-MAX_OUTPUT:],
                    "decision": d.to_dict()}
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "exit_code": None, "decision": d.to_dict()}

    def run_command(self, bot_id: str, session_id: str | None, cmd: str) -> dict:
        if session_id:
            self._session(bot_id, session_id)
        return self._run(bot_id, cmd, "mcp:bot_run_command")

    def run_tests(self, bot_id: str, session_id: str | None, test_cmd: str | None = None) -> dict:
        if session_id:
            self._session(bot_id, session_id)
        cmd = test_cmd or self.s.sandbox_config(bot_id).get("default_test_cmd") or "python3 -m unittest discover -s tests -v"
        res = self._run(bot_id, cmd, "mcp:bot_run_tests")
        res["test_cmd"] = cmd
        res["passed"] = res.get("status") == "ok"
        return res
