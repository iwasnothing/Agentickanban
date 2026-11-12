import pytest

from scripts.config import load_settings
from scripts.ticket_sync import md_utils as md
from scripts.ticket_sync.store import TicketError, TicketStore
from scripts.triage import triage


def test_md_roundtrip():
    meta = {"id": "x", "title": "A: tricky # title", "status": "backlog", "labels": ["a", "b"], "github_issue": None}
    body = md.build_body({"Objective": "do it", "Acceptance Criteria": "- [ ] one\n- [x] two"})
    m2, b2 = md.parse(md.render(meta, body))
    assert m2["title"] == "A: tricky # title" and m2["labels"] == ["a", "b"] and m2["github_issue"] is None
    assert md.acceptance_criteria(b2) == [{"done": False, "text": "one"}, {"done": True, "text": "two"}]


def test_lifecycle(control):
    st = TicketStore(load_settings())
    att = control["root"] / "spec.txt"; att.write_text("spec")
    r = st.create("Add login rate limit", "Throttle logins", acceptance_criteria=["429 on limit"],
                  context=["src/auth"], attachments=[str(att)], priority="high", labels=["backend"])
    tid = r["ticket_id"]
    assert r["path"] == f"tickets/backlog/{tid}.md"
    assert r["github"]["mode"] == "dry-run"
    assert (control["root"] / "attachments" / tid / "spec.txt").exists()
    # duplicate titles get unique ids
    assert st.create("Add login rate limit", "again")["ticket_id"] == tid + "-2"
    st.assign(tid, "bot-001")
    with pytest.raises(TicketError):
        st.assign(tid, "bot-999")
    mv = st.move(tid, "todo")  # alias
    assert mv["to"] == "to-do"
    t = st.get(tid)
    assert t["metadata"]["status"] == "to-do" and t["metadata"]["assignee_bot"] == "bot-001"
    assert t["path"].startswith("tickets/to-do/")
    st.update(tid, {"priority": "low", "acceptance_criteria": ["a", "b"]})
    assert [c["text"] for c in st.get(tid)["acceptance_criteria"]] == ["a", "b"]
    with pytest.raises(TicketError):
        st.update(tid, {"status": "done"})
    st.add_log(tid, "Turn 1 (bot-001)", ["Prompt sent"])
    st.add_review_note(tid, "Gap: missing tests\nAction: request changes")
    md_text = st.get(tid)["markdown"]
    assert "## Turn 1 Review (superagent)" in md_text and "Gap: missing tests" in md_text
    st.move(tid, "done")
    assert [x["id"] for x in st.list(status="done")] == [tid]
    assert [x["id"] for x in st.list(assignee_bot="bot-001")] == [tid]
    with pytest.raises(TicketError):
        st.move(tid, "nowhere")


def test_front_matter_mirrors_folder(control):
    st = TicketStore(load_settings())
    tid = st.create("T", "o")["ticket_id"]
    p = control["root"] / "tickets" / "backlog" / f"{tid}.md"
    p.write_text(p.read_text().replace("status: backlog", "status: done"))
    assert st.get(tid)["metadata"]["status"] == "backlog"  # folder is canonical


def test_triage(control):
    st = TicketStore(load_settings())
    tid = st.create("Add python unittest for greet", "Write tests", labels=["python"])["ticket_id"]
    r = triage(st, tid, apply=True)
    assert r["suggested_bot"] == "bot-001" and r["applied"]
    assert st.get(tid)["metadata"]["status"] == "to-do"
    tid2 = st.create("Epic: architecture overhaul", "break down the system")["ticket_id"]
    assert triage(st, tid2)["suggested_column"] == "planning"
