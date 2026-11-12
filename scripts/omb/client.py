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

    def wait(self, bot_id: str, task_id: str, timeout_s: int = 600) -> dict:
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
