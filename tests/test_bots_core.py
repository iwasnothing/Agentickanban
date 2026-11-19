import subprocess

from scripts.bots.core import BotManager
from scripts.config import load_settings
from scripts.ticket_sync.store import TicketStore


class FakeOMB:
    def __init__(self):
        self.sent = []

    def bots(self):
        return [{"id": "8a7348b7-596f-4a9a-8c13-fa57a262ceaf", "name": "MausBot", "activity": "idle", "busy": False}]

    def create_task(self, bot_id, title, cwd):
        self.cwd = cwd
        return {"threadId": "task-123", "cwd": cwd}

    def send(self, bot_id, task_id, text, send_id=None):
        self.sent.append(text)
        (self_ws := __import__("pathlib").Path(self.cwd))
        (self_ws / "hello.py").write_text("print('hello')\n")  # simulate the bot editing
        return {"messageId": "m1"}

    def wait(self, bot_id, task_id, timeout_s=600):
        return {"status": "done", "exit_code": 0}

    def last_reply(self, bot_id, task_id):
        return {"reply": "Created hello.py", "digest": "[digest] write hello.py"}


def test_full_turn(control):
    s = load_settings()
    st = TicketStore(s)
    tid = st.create("Add hello", "Create hello.py")["ticket_id"]
    fake = FakeOMB()
    m = BotManager(s, st, fake)
    assert m.list()[0]["openmausbot_bot"]["name"] == "MausBot"
    sess = m.start_session("bot-001", tid)
    ws = control["workspaces"] / "bot-001"
    assert sess["workspace"] == str(ws) and fake.cwd == str(ws)
    assert (ws / ".opencode" / "plugins" / "kanban-permission-gate.js").exists()
    assert (ws / ".ticket" / "TICKET.md").exists()
    assert subprocess.run(["git", "-C", str(ws), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip() == "bots/bot-001"
    r = m.send_prompt("bot-001", sess["session_id"], "Create hello.py")
    assert r["status"] == "done" and r["turn"] == 1 and "AGENTS.md" in fake.sent[0]
    d = m.get_last_turn_diff("bot-001", sess["session_id"])
    assert d["files_changed"] == ["hello.py"] and "+print('hello')" in d["diff"]
    assert ".ticket" not in d["diff"] and ".opencode" not in d["diff"]
    t = m.run_tests("bot-001", sess["session_id"])
    assert t["passed"], t
    assert m.run_command("bot-001", sess["session_id"], "pip install x")["status"] == "needs_approval"
    assert m.run_command("bot-001", sess["session_id"], "rm -rf /")["status"] == "blocked"
    c = m.commit_turn("bot-001", sess["session_id"])
    assert c["committed"] and c["message"] == f"bot(bot-001): turn 1 for ticket {tid}" and c["pushed"] is False
    d2 = m.get_last_turn_diff("bot-001", sess["session_id"])
    assert d2["mode"] == "committed" and d2["files_changed"] == ["hello.py"]
    assert m.write_file("bot-001", "notes.txt", "x")["bytes"] == 1
    assert m.read_file("bot-001", "notes.txt")["content"] == "x"
    tree = m.get_workspace_tree("bot-001")
    assert "hello.py" in tree["files"] and "notes.txt" in tree["files"]
    log = st.get(tid)["markdown"]
    assert "## Turn 1 (bot-001)" in log and "## Turn 1 commit (bot-001)" in log


def test_send_prompt_logs_progress_instead_of_blank_reply(control):
    s = load_settings()
    s.raw.setdefault("openmausbot", {})
    s.raw["openmausbot"]["progress_log_seconds"] = 1
    s.raw["openmausbot"]["default_wait_seconds"] = 2
    s.raw["openmausbot"]["max_wait_seconds"] = 2
    st = TicketStore(s)
    tid = st.create("Heavy task", "Do a long research turn")["ticket_id"]

    class FakeBusyOMB(FakeOMB):
        def wait(self, bot_id, task_id, timeout_s=600):
            return {"status": "timed-out", "exit_code": 4}

        def last_reply(self, bot_id, task_id):
            return {"reply": None, "digest": None}

        def activity_snapshot(self, bot_id, task_id):
            return {
                "tool_counts": {"execute": 12, "websearch": 3},
                "tool_total": 15,
                "latest": "execute",
                "active": True,
                "summary": "15 tools so far (execute ×12, websearch ×3); latest: execute; turn still running",
                "fingerprint": "15:execute:0:0",
                "reply": None,
            }

    m = BotManager(s, st, FakeBusyOMB())
    sess = m.start_session("bot-001", tid)
    r = m.send_prompt("bot-001", sess["session_id"], "Research and implement")
    assert r["status"] == "timed-out"
    log = st.get(tid)["markdown"]
    assert "Bot reply: (none)" not in log
    assert "## Turn 1 progress (bot-001)" in log
    assert "Still working" in log
    assert "execute ×12" in log and "websearch ×3" in log
    assert "In-progress activity:" in log
    assert "No finished text reply yet" in log


def test_activity_snapshot_from_events(tmp_path, monkeypatch):
    from scripts.omb.client import OpenMausBot, _snapshot_from_events, format_activity

    ev = tmp_path / "events"
    ev.mkdir()
    path = ev / "task-abc.ndjson"
    path.write_text(
        "\n".join([
            '{"type":"turn.started","createdAt":"2026-11-15T12:13:05.000Z"}',
            '{"type":"item.started","itemType":"tool","title":"websearch","createdAt":"2026-11-15T12:13:10.000Z"}',
            '{"type":"item.started","itemType":"tool","title":"execute","createdAt":"2026-11-15T12:13:20.000Z"}',
            '{"type":"item.started","itemType":"tool","title":"execute","createdAt":"2026-11-15T12:23:11.000Z"}',
        ]) + "\n"
    )
    snap = _snapshot_from_events(path)
    assert snap["tool_total"] == 3
    assert snap["tool_counts"] == {"websearch": 1, "execute": 2}
    assert snap["latest"] == "execute" and snap["active"] is True and not snap["reply"]
    assert "3 tools so far (execute ×2, websearch ×1); latest: execute; turn still running" == format_activity(snap)

    monkeypatch.setenv("OPENMAUSBOT_HOME", str(tmp_path))
    omb = OpenMausBot()
    omb.last_reply = lambda bot_id, task_id: {"reply": None, "digest": None}  # type: ignore
    live = omb.activity_snapshot("bot", "task-abc")
    assert live["tool_total"] == 3 and "execute ×2" in live["summary"]
