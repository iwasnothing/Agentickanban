"""Thin client for the local OpenMausBot server (HTTP API + `openmausbot` headless CLI).

The superagent never talks to this directly: mcp_servers/bots_mcp.py uses it to map
Kanban bot ids onto OpenMausBot bots. Sessions are OpenMausBot tasks (conversations)
created with `cwd` = the bot's git worktree, so the coding engine works there.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


class OMBError(RuntimeError):
    pass


class OpenMausBot:
    def __init__(self, url: str = "http://127.0.0.1:8799", cli: str = "openmausbot", timeout: float = 30):
        self.url = url.rstrip("/")
        self.cli = cli
        self.timeout = timeout
        port = self.url.rsplit(":", 1)[-1].split("/")[0]
        self.env = {**os.environ, "OMB_PORT": port if port.isdigit() else "8799"}

    # ---------- HTTP ----------
    def _http(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"content-type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise OMBError(f"OpenMausBot {method} {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as e:
            raise OMBError(f"OpenMausBot not reachable at {self.url}: {e.reason}")

    def health(self) -> bool:
        try:
            self._http("GET", "/api/bots")
            return True
        except OMBError:
            return False

    def bots(self) -> list[dict]:
        return (self._http("GET", "/api/bots") or {}).get("bots", [])

    def bot(self, bot_id: str) -> dict:
        for b in self.bots():
            if b["id"] == bot_id:
                return b
        raise OMBError(f"OpenMausBot has no bot {bot_id}")

    def create_task(self, bot_id: str, title: str, cwd: str) -> dict:
        """New conversation for the bot whose coding engine works in `cwd`."""
        res = self._http("POST", f"/api/bots/{bot_id}/tasks", {"title": title[:120], "cwd": cwd, "select": False})
        return res["task"]

    # ---------- CLI ----------
    def _cli(self, args: list[str], timeout: float) -> tuple[int, Any, str]:
        p = subprocess.run([self.cli, *args, "--json", "--quiet"], capture_output=True, text=True,
                           timeout=timeout, env=self.env)
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else None
        except json.JSONDecodeError:
            data = {"raw": p.stdout[-2000:]}
        return p.returncode, data, p.stderr[-2000:]

    def send(self, bot_id: str, task_id: str, text: str, send_id: str | None = None) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            path = f.name
        try:
            args = ["send", "--bot", bot_id, "--task", task_id, "--message-file", path]
            if send_id:
                args += ["--send-id", send_id]
            code, data, err = self._cli(args, timeout=60)
        finally:
            os.unlink(path)
        if code != 0:
            raise OMBError(f"send failed (exit {code}): {data or err}")
        return data or {}

    def wait(self, bot_id: str, task_id: str, timeout_s: int = 1800) -> dict:
        code, data, err = self._cli(["wait", "--bot", bot_id, "--task", task_id, "--for", "turn",
                                     "--timeout", f"{int(timeout_s)}s", "--on-input", "exit"],
                                    timeout=timeout_s + 60)
        status = {0: "done", 1: "failed", 3: "needs-user", 4: "timed-out"}.get(code, f"exit-{code}")
        return {"status": status, "exit_code": code, "result": data, **({"stderr": err} if code not in (0, 4) else {})}

    def messages(self, bot_id: str, task_id: str, limit: int = 30) -> dict:
        code, data, err = self._cli(["messages", "--bot", bot_id, "--task", task_id, "--limit", str(limit)], timeout=60)
        if code != 0:
            raise OMBError(f"messages failed (exit {code}): {data or err}")
        return data or {}

    def last_reply(self, bot_id: str, task_id: str) -> dict:
        """The bot's latest text reply and turn digest (tool activity summary)."""
        msgs = self.messages(bot_id, task_id, limit=20).get("messages") or []
        reply = digest = None
        for m in reversed(msgs):
            if m.get("role") == "user":
                break
            if m.get("role") != "bot":
                continue
            if m.get("kind") == "digest" and digest is None:
                digest = m.get("text")
            elif m.get("kind") == "text" and reply is None:
                reply = m.get("text")
        return {"reply": reply, "digest": digest}

    @staticmethod
    def events_path(task_id: str) -> Path:
        root = Path(os.environ.get("OPENMAUSBOT_HOME") or (Path.home() / ".openmausbot"))
        return root / "events" / f"{task_id}.ndjson"

    def activity_snapshot(self, bot_id: str, task_id: str) -> dict:
        """In-progress tool activity for a turn, used when there is no finished text reply yet."""
        snap = _snapshot_from_events(self.events_path(task_id))
        try:
            reply_info = self.last_reply(bot_id, task_id)
        except Exception:
            reply_info = {}
        if reply_info.get("reply") and not snap.get("reply"):
            snap["reply"] = reply_info["reply"]
        if reply_info.get("digest") and not snap.get("digest"):
            snap["digest"] = reply_info["digest"]
        snap["summary"] = format_activity(snap)
        snap["fingerprint"] = (
            f"{snap.get('tool_total', 0)}:{snap.get('latest') or ''}:"
            f"{int(bool(snap.get('turn_complete')))}:{int(bool(snap.get('reply')))}"
        )
        return snap


def format_activity(snap: dict) -> str:
    """Short, secret-free line describing tool activity for the ticket log."""
    counts = snap.get("tool_counts") or {}
    total = int(snap.get("tool_total") or sum(counts.values()) or 0)
    if total and counts:
        parts = ", ".join(
            f"{name} ×{n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        text = f"{total} tools so far ({parts})"
        if snap.get("latest"):
            text += f"; latest: {snap['latest']}"
    elif total:
        text = f"{total} tools so far"
    else:
        text = "no tool calls recorded yet"
    if snap.get("turn_complete"):
        text += "; turn finished"
    elif snap.get("active"):
        text += "; turn still running"
    return text


def _snapshot_from_events(path: Path) -> dict:
    counts: Counter[str] = Counter()
    latest = latest_at = last_text = None
    turn_complete = False
    turn_started = False
    if path.is_file():
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = row.get("type")
                created = row.get("createdAt")
                if typ == "turn.started":
                    turn_started = True
                    turn_complete = False
                elif typ == "turn.completed":
                    turn_complete = True
                elif typ == "item.started" and row.get("itemType") == "tool":
                    name = (row.get("title") or "tool").strip() or "tool"
                    if len(name) > 80:
                        name = name[:77] + "..."
                    counts[name] += 1
                    latest, latest_at = name, created
                elif typ == "item.completed" and row.get("itemType") == "assistant_text":
                    text = (row.get("text") or "").strip()
                    if text:
                        last_text = text
    total = sum(counts.values())
    return {
        "tool_counts": dict(counts),
        "tool_total": total,
        "latest": latest,
        "latest_at": latest_at,
        "turn_complete": turn_complete,
        "active": turn_started and not turn_complete,
        "reply": last_text,
    }
