import os

from scripts.config import load_settings
from scripts.ticket_sync.github_sync import GitHubSync
from scripts.ticket_sync.store import TicketStore


def test_dry_run_logs(control):
    os.environ["AIBOTS_GITHUB_SYNC"] = "0"
    st = TicketStore(load_settings())
    tid = st.create("Sync me", "obj", labels=["x"])["ticket_id"]
    r = GitHubSync(st).sync(tid)
    assert r["mode"] == "dry-run" and "create issue" in r["actions"][0]
    assert "[dry-run]" in (control["state"] / "github-sync.log").read_text()
    del os.environ["AIBOTS_GITHUB_SYNC"]


def test_live_with_fake_transport(control, monkeypatch):
    monkeypatch.setenv("AIBOTS_GITHUB_SYNC", "0")
    st = TicketStore(load_settings())
    tid = st.create("Sync me", "obj", priority="high")["ticket_id"]
    st.assign(tid, "bot-001")
    calls = []
    fields = [{"id": "F1", "name": "Status", "options": [{"id": "o-todo", "name": "To-Do"}, {"id": "o-done", "name": "Done"},
                                                           {"id": "o-back", "name": "Backlog"}]},
              {"id": "F2", "name": "Bot", "options": [{"id": "o-b1", "name": "bot-001"}]},
              {"id": "F3", "name": "Priority", "options": [{"id": "o-h", "name": "High"}]}]

    def http(method, url, body):
        calls.append((method, url, body))
        if url.endswith("/issues") and method == "POST":
            return 201, {"number": 7, "node_id": "I_7"}
        if url == "/graphql":
            q = body["query"]
            if "organization(login" in q:
                return 200, {"data": {"organization": {"projectV2": {"id": "P1", "fields": {"nodes": fields}}}}}
            if "addProjectV2ItemById" in q:
                return 200, {"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_1"}}}}
            return 200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_1"}}}}
        if method == "PATCH":
            return 200, {"number": 7, "node_id": "I_7"}
        return 404, {"message": "unexpected"}

    g = GitHubSync(st, token="t", repo="me/repo", project="me/3", transport=http)
    r = g.sync(tid)
    assert r["mode"] == "live" and r["issue"] == 7 and r["project_item"] == "PVTI_1"
    meta = st.get(tid)["metadata"]
    assert meta["github_issue"] == 7 and meta["github_project_item_id"] == "PVTI_1"
    post = calls[0]
    assert post[0] == "POST" and "bot/bot-001" in post[2]["labels"] and "status/backlog" in post[2]["labels"]
    opts = [c[2]["variables"].get("o") for c in calls if c[1] == "/graphql" and "updateProjectV2ItemFieldValue" in c[2]["query"]]
    assert opts == ["o-back", "o-b1", "o-h"]
    # move to done -> issue closed, status Done
    st.move(tid, "done")
    calls.clear()
    GitHubSync(st, token="t", repo="me/repo", project="me/3", transport=http).sync(tid)
    patch = [c for c in calls if c[0] == "PATCH"][0]
    assert patch[2]["state"] == "closed"
    assert "o-done" in [c[2]["variables"].get("o") for c in calls if c[1] == "/graphql"]
