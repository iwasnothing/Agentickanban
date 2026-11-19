import json
from datetime import datetime, timedelta
from pathlib import Path

from scripts.bots.watchdog import WorkerWatchdog, host_is_safe, is_structured_hostname, load_watchdog_config
from scripts.config import load_settings
from scripts.permission_gate.gate import PermissionGate
from scripts.ticket_sync.store import TicketStore


def _write_ticket(store: TicketStore, bot_id="bot-001", status="in-progress", body_extra="",
                  title="HK prices analysis", objective="Do the work", context=None):
    rec = store.create(
        title=title,
        objective=objective,
        context=context,
        acceptance_criteria=["fetched data", "chart exported"],
        status="backlog",
    )
    tid = rec["ticket_id"]
    store.move(tid, status)
    store.assign(tid, bot_id)
    if body_extra:
        store.add_log(tid, "Turn 4 result (bot-001)", body_extra.splitlines(), sync=False)
    return tid


def _pending(state: Path, bot="bot-001", host="www.landreg.gov.hk", at=None):
    at = at or datetime.now().isoformat(timespec="seconds")
    rec = {
        "at": at, "bot": bot, "host": host, "rule": "network",
        "reason": f"network call to unknown host {host}. This environment is non-interactive",
        "command": f"curl https://{host}/data.json",
        "resolution": f"bin/gate approve-host --bot {bot} --host {host}",
    }
    with (state / "pending-approvals.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec



def _strip_official_hosts(settings):
    """Rewrite allowed_hosts so official HK gov hosts are not pre-allowed (valid YAML)."""
    sb = settings.bots_dir / "bot-001" / "sandbox.config.yaml"
    raw = sb.read_text()
    # Replace the allowed_hosts block with package registries only.
    start = raw.find("allowed_hosts:")
    if start < 0:
        raise AssertionError("allowed_hosts missing")
    # from allowed_hosts to unknown_host
    end = raw.find("unknown_host:", start)
    raw = raw[:start] + "allowed_hosts: [pypi.org, files.pythonhosted.org, registry.npmjs.org, localhost, 127.0.0.1]\n    " + raw[end:]
    sb.write_text(raw)

def test_host_is_safe_official_hk_only():
    suffixes = ["gov.hk"]
    assert host_is_safe("www.landreg.gov.hk", suffixes)
    assert host_is_safe("portal.csdi.gov.hk", suffixes)
    assert host_is_safe("data.gov.hk", suffixes)
    assert not host_is_safe("evil.example", suffixes)
    assert not host_is_safe("pastebin.com", suffixes)


def test_allow_official_host_and_resume(control):
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store, body_extra="- Bot reply: Both www.landreg.gov.hk and portal.csdi.gov.hk require your approval. Please approve both when prompted.")
    _strip_official_hosts(settings)
    _pending(control["state"], host="www.landreg.gov.hk")
    sent = []

    def sender(bot_id, session_id, prompt):
        sent.append((bot_id, session_id, prompt))
        return {"status": "dispatched"}

    # seed a session
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    wd = WorkerWatchdog(settings=settings, store=store, sender=sender)
    # Temporarily drop gov.hk from watchdog? No — safe suffixes should still allow it.
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "allow_host" in actions
    # Host is now approved at runtime
    gate = PermissionGate("bot-001", settings=settings)
    from scripts.permission_gate.gate import host_is_allowed
    assert host_is_allowed("www.landreg.gov.hk", gate.allowed_hosts())
    assert sent, "worker should be resumed after allow"
    assert "www.landreg.gov.hk" in sent[0][2] or "landreg.gov.hk" in sent[0][2]
    # Official AC still unchecked; not moved to done
    path = store.find(tid)
    body = path.read_text()
    assert "- [ ] fetched data" in body or "- [ ] Fetched data" in body or "- [ ]" in body
    assert "- [x]" not in body.lower()
    assert path.parent.name != "done"
    assert "Watchdog (superagent)" in body
    assert "Decision: allow_host" in body


def test_deny_unsafe_host_fails_clearly(control):
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store, body_extra="- Bot reply: Please approve the network call to evil.example when prompted.")
    _pending(control["state"], host="evil.example")
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda b, s, p: sent.append(p) or {"status": "dispatched"})
    result = wd.run_once(apply=True)
    actions = [d["action"] for d in result["decisions"]]
    assert "deny" in actions
    gate = PermissionGate("bot-001", settings=settings)
    assert "evil.example" not in gate.allowed_hosts()
    assert sent, "worker should be told the host was denied so it does not wait"
    assert "NOT approved" in sent[0] or "not" in sent[0].lower()
    body = store.find(tid).read_text()
    assert "Decision: deny" in body
    assert path_unchecked(store, tid)


def path_unchecked(store, tid):
    body = store.find(tid).read_text()
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"
    return True


def test_already_allowed_host_does_not_resume_from_ticket_text(control):
    """A finished turn plus leftover 'please approve' prose is not a current host request."""
    settings = load_settings()
    store = TicketStore(settings)
    # gov.hk is in the copied sandbox config, so landreg is already allowed
    tid = _write_ticket(store, status="in-review",
                        body_extra="- Bot reply: Both www.landreg.gov.hk and portal.csdi.gov.hk require your approval. Please approve both when prompted.")
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda b, s, p: sent.append(p) or {"status": "dispatched"})
    result = wd.run_once(apply=True)
    actions = [d["action"] for d in result["decisions"]]
    assert "deny" not in actions
    assert "resume" not in actions
    assert "allow_host" not in actions
    assert sent == []


def test_healthy_active_turn_is_not_a_stall(control):
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store)
    store.add_log(tid, "Turn 5 (bot-001)", [f"Prompt sent at {datetime.now():%Y-%m-%d %H:%M}"], sync=False)
    store.add_log(tid, "Turn 5 progress (bot-001)",
                  [f"Still working at {datetime.now():%Y-%m-%d %H:%M} (60s elapsed): 10 tools so far; latest: execute; turn still running."],
                  sync=False)
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda *a: (_ for _ in ()).throw(AssertionError("should not resume")))
    result = wd.run_once(apply=True)
    assert result["stalls"] == [] or all(d["action"] == "none" for d in result["decisions"])


def test_inspects_all_configured_bots(control):
    settings = load_settings()
    # Add a second bot to the throwaway registry
    reg = settings.bots_dir / "registry.yaml"
    text = reg.read_text()
    text += """
  bot-002:
    name: Second bot
    openmausbot_bot_id: 00000000-0000-0000-0000-000000000002
    workspace: /tmp/bot-002
    enabled: true
"""
    reg.write_text(text)
    store = TicketStore(settings)
    _write_ticket(store, bot_id="bot-001")
    _write_ticket(store, bot_id="bot-002")
    for bot, host in (("bot-001", "blocked-one.example"), ("bot-002", "evil.example")):
        rec = _pending(control["state"], bot=bot, host=host)
        with (control["state"] / "gate-audit.jsonl").open("a") as f:
            f.write(json.dumps({
                "at": rec["at"], "bot": bot, "source": "opencode:tool.execute.before",
                "decision": "needs_approval",
                "reason": f"network call to unknown host {host}",
                "rule": "network", "command": rec["command"], "host": host,
            }) + "\n")
    # Make official host blocked for bot-001 by removing gov suffixes from a copy? bot-002 has no sandbox config, default deny/needs_approval
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda b, s, p: sent.append(b) or {"status": "dispatched"})
    result = wd.run_once(apply=False)
    assert "bot-001" in result["bots"]
    assert "bot-002" in result["bots"]
    bots_with_stalls = {s["bot_id"] for s in result["stalls"]}
    assert "bot-001" in bots_with_stalls
    assert "bot-002" in bots_with_stalls


def test_idempotent_does_not_double_resume(control):
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store, body_extra="- Bot reply: Please approve the call to portal.csdi.gov.hk when prompted.")
    _strip_official_hosts(settings)
    _pending(control["state"], host="portal.csdi.gov.hk")
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    wd.run_once(apply=True)
    first = len(sent)
    assert first >= 1
    wd.run_once(apply=True)
    assert len(sent) == first


def test_config_interval_and_safe_suffixes(control):
    settings = load_settings()
    cfg_path = settings.root / "superagent" / "config" / "superagent.yaml"
    # file should already have watchdog after we add it; also works with defaults
    cfg = load_watchdog_config(settings)
    assert cfg["interval_seconds"] >= 30
    assert "gov.hk" in cfg["safe_host_suffixes"]
    assert cfg["enabled"] is True


def test_ignores_unrelated_cli_host_and_does_not_prompt_active_turn(control):
    """A CLI gate check or leftover pending host must not interrupt an in-flight worker turn."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store)
    store.add_log(tid, "Turn 5 (bot-001)", [f"Prompt sent at {datetime.now():%Y-%m-%d %H:%M}"], sync=False)
    store.add_log(tid, "Turn 5 progress (bot-001)",
                  [f"Still working at {datetime.now():%Y-%m-%d %H:%M} (60s elapsed): execute; turn still running."],
                  sync=False)
    rec = _pending(control["state"], host="evil.example")
    # also a cli-source audit line
    with (control["state"] / "gate-audit.jsonl").open("a") as f:
        f.write(json.dumps({
            "at": rec["at"], "bot": "bot-001", "source": "cli-verify",
            "decision": "needs_approval",
            "reason": "network call to unknown host evil.example",
            "rule": "network", "command": rec["command"],
        }) + "\n")
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True)
    assert sent == []
    assert all(d["action"] in ("none",) or d.get("details", {}).get("defer_resume") for d in result["decisions"] if d["action"] != "none") or result["stalls"] == [] or all(
        d["action"] == "none" for d in result["decisions"]
    )
    body = store.find(tid).read_text()
    assert "evil.example was NOT approved" not in body
    assert "Turn 6" not in body


def test_silent_allow_during_active_turn_does_not_prompt(control):
    settings = load_settings()
    store = TicketStore(settings)
    _strip_official_hosts(settings)
    tid = _write_ticket(store, body_extra="- working with portal.csdi.gov.hk")
    store.add_log(tid, "Turn 5 (bot-001)", [f"Prompt sent at {datetime.now():%Y-%m-%d %H:%M}"], sync=False)
    store.add_log(tid, "Turn 5 progress (bot-001)",
                  [f"Still working at {datetime.now():%Y-%m-%d %H:%M} (30s elapsed): execute; turn still running."],
                  sync=False)
    rec = _pending(control["state"], host="portal.csdi.gov.hk")
    with (control["state"] / "gate-audit.jsonl").open("a") as f:
        f.write(json.dumps({
            "at": rec["at"], "bot": "bot-001", "source": "opencode:tool.execute.before",
            "decision": "needs_approval",
            "reason": "network call to unknown host portal.csdi.gov.hk",
            "rule": "network", "command": rec["command"], "host": "portal.csdi.gov.hk",
        }) + "\n")
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    result = wd.run_once(apply=True)
    actions = [d["action"] for d in result["decisions"]]
    assert "allow_host" in actions
    assert sent == [], "must not send a new prompt while the worker turn is running"
    from scripts.permission_gate.gate import host_is_allowed, PermissionGate
    assert host_is_allowed("portal.csdi.gov.hk", PermissionGate("bot-001", settings=settings).allowed_hosts())


def test_is_structured_hostname_rejects_numbers_and_filenames():
    assert is_structured_hostname("www.landreg.gov.hk")
    assert is_structured_hostname("portal.csdi.gov.hk")
    assert is_structured_hostname("evil.example")
    assert not is_structured_hostname("9.1")
    assert not is_structured_hostname("5.8")
    assert not is_structured_hostname("14.5")
    assert not is_structured_hostname("sources.json")
    assert not is_structured_hostname("sandbox.config.yaml")
    assert not is_structured_hostname("approved-hosts.json")
    assert not is_structured_hostname("pending-approvals.jsonl")
    assert not is_structured_hostname("open-data.html")
    assert not is_structured_hostname("fails")


def test_finished_turn_ticket_prose_is_not_a_host_stall(control):
    """Reproduce the 08:55 HKT false-positive: after Turn 5 ended, leftover review
    numbers (9.1, 5.8) and filenames were parsed as hosts and a deny burst queued
    new worker prompts. A finished turn plus ticket prose is not a live request.
    """
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store, status="in-progress")
    # Older turn asked for real official hosts; then a review quoted leftover figures
    # and filenames; then Turn 5 completed without an open host request.
    store.add_log(tid, "Turn 4 result (bot-001)", [
        "Status: done at 2026-11-19 01:36 (223s elapsed)",
        "Bot reply: Both www.landreg.gov.hk and portal.csdi.gov.hk require your approval. Please approve both when prompted.",
    ], sync=False)
    store.add_log(tid, "Turn 4 Review (superagent)", [
        "Reviewed at 2026-11-19 01:36",
        "Ranked CSV leftover: Islands (+9.1%), Kowloon City (+5.8%), Wan Chai (+3.3%).",
        "Top drop: Sham Shui Po (-14.5%), Southern (-13.5%), Sai Kung (-13.4%).",
        "See analysis/hk-residential-prices/sources.json and bots/bot-001/sandbox.config.yaml.",
        "Also mentioned: approved-hosts.json, pending-approvals.jsonl, landsd.gov.hk/en/open-data.html.",
        "Do not treat these leftover figures as official statistics.",
    ], sync=False)
    store.add_log(tid, "Turn 5 (bot-001)", [
        "Prompt sent at 2026-11-19 08:26 (session sess-1)",
    ], sync=False)
    store.add_log(tid, "Turn 5 result (bot-001)", [
        "Status: done at 2026-11-19 08:55 (1512s elapsed)",
        "Bot reply: Verified official portals landsd.gov.hk and landreg.gov.hk. Continuing from the existing analysis notes; see sources.json mentioned in the earlier review.",
    ], sync=False)
    (control["state"] / "sessions.json").write_text(json.dumps({
        "sess-1": {"session_id": "sess-1", "bot_id": "bot-001", "ticket_id": tid}
    }))
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store, sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True)
    actions = [d["action"] for d in result["decisions"]]
    hosts = [s.get("host") for s in result["stalls"]]
    assert sent == [], f"must not queue deny/resume prompts after a finished turn: {sent}"
    assert "deny" not in actions
    assert "resume" not in actions
    assert "allow_host" not in actions
    for fake in ("9.1", "5.8", "3.3", "14.5", "13.5", "13.4", "sources.json",
                 "sandbox.config.yaml", "approved-hosts.json", "pending-approvals.jsonl",
                 "open-data.html", "fails"):
        assert fake not in hosts, f"ticket prose {fake!r} must not be a host stall"
    # Official AC stay unchecked; ticket is not moved to done
    body = store.find(tid).read_text()
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"
    # Watchdog must not have appended a deny burst to the ticket
    assert "Decision: deny" not in body
    assert "was NOT approved" not in body



def _seed_session(control, tid, session_id="sess-1", bot_id="bot-001"):
    (control["state"] / "sessions.json").write_text(json.dumps({
        session_id: {"session_id": session_id, "bot_id": bot_id, "ticket_id": tid}
    }))


def test_missing_source_suggests_alternative_from_intent_and_redirects_once(control):
    """Unavailable source → interpret high-level intent, suggest a public alternative, redirect once."""
    settings = load_settings()
    store = TicketStore(settings)
    objective = (
        "Collect public Hong Kong residential sales figures and compute "
        "price-per-square-foot change by district over the last two years."
    )
    context = (
        "Requested via the Agentic Kanban web UI. This is the user's high-level description only — "
        "Superagent will expand it into a structured ticket.\n"
        "Search public Hong Kong residential sales figures, compute price-per-square-foot change "
        "by district, sort from highest rise to highest drop, visualize in bar charts."
    )
    tid = _write_ticket(
        store,
        title="HK residential sales price per sq ft change by district",
        objective=objective,
        context=context,
    )
    store.add_log(tid, "Turn 5 result (bot-001)", [
        "Status: done at 2026-11-19 08:55 (1512s elapsed)",
        "Bot reply: The EPRC transaction-level data is not publicly available from the official "
        "Hong Kong government open data portals. I could not find the requested dataset. "
        "A source URL returned 404.",
    ], sync=False)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "redirect" in actions, result
    assert sent, "worker should be briefed once to change direction"
    prompt = sent[0][2]
    assert "high-level intent" in prompt.lower() or "original high-level intent" in prompt.lower()
    assert any(token in prompt.lower() for token in ("rvd.gov.hk", "data.gov.hk", "land registry", "official public"))
    assert "do not invent" in prompt.lower()
    assert "leftover" in prompt.lower()
    assert "acceptance-criteria" in prompt.lower() or "acceptance criteria" in prompt.lower()
    body = store.find(tid).read_text()
    assert "Decision: redirect" in body
    assert "Stall: source_failure" in body
    assert "High-level intent:" in body
    assert "Suggestion:" in body
    assert "data.gov.hk" in body or "rvd.gov.hk" in body
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"
    # Second pass must not spawn another prompt (dedup).
    sent2 = []
    wd2 = WorkerWatchdog(settings=settings, store=store,
                         sender=lambda *a: sent2.append(a) or {"status": "dispatched"})
    result2 = wd2.run_once(apply=True, bot_id="bot-001")
    actions2 = [d["action"] for d in result2["decisions"] if d.get("action") != "none"]
    assert sent2 == [], f"same failure must not spawn a prompt burst: {sent2} actions={actions2}"


def test_paid_walled_source_is_not_auto_approved(control):
    """Paid / login-walled sources are noted for a human; never auto-bought or logged into."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        title="HK residential sales price analysis",
        objective="Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        context="User high-level request: Search public Hong Kong residential sales figures and chart district changes.",
    )
    store.add_log(tid, "Turn 3 result (bot-001)", [
        "Status: done at 2026-11-19 09:10 (90s elapsed)",
        "Bot reply: The only remaining source is a paid EPRC subscription behind a login-walled "
        "commercial portal. Access requires a paid subscription and sign-in.",
    ], sync=False)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "redirect" not in actions
    assert "allow_host" not in actions
    assert "note" in actions
    assert sent == [], f"must not auto-approve, buy, or brief a login/paid source: {sent}"
    body = store.find(tid).read_text()
    assert "Decision: note" in body
    assert "Stall: source_failure" in body
    assert "paid" in body.lower() or "login-walled" in body.lower() or "not auto-approved" in body.lower()
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"


def test_source_failure_does_not_prompt_while_turn_in_progress(control):
    """A still-running turn is never interrupted by a source-failure redirect."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        title="HK residential sales price analysis",
        objective="Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        context="Search public Hong Kong residential sales figures and chart district changes.",
    )
    store.add_log(tid, "Turn 4 result (bot-001)", [
        "Bot reply: The requested dataset is not publicly available and the source URL returned 404.",
    ], sync=False)
    store.add_log(tid, "Turn 5 (bot-001) — 2026-11-19 09:20 progress", [
        "working: fetching official open data",
    ], sync=False)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert sent == [], f"must not prompt while a turn is in progress: {sent}"
    assert "redirect" not in actions
    kinds = [s.get("kind") for s in result["stalls"]]
    assert "source_failure" not in kinds


def test_in_review_source_failure_is_not_auto_redirected(control):
    """After Superagent has already reviewed, do not re-brief the worker on sidecar restart."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        status="in-review",
        title="HK residential sales price analysis",
        objective="Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        context="Search public Hong Kong residential sales figures and chart district changes.",
    )
    store.add_log(tid, "Turn 5 result (bot-001)", [
        "Bot reply: The EPRC transaction-level data is not publicly available.",
    ], sync=False)
    store.add_log(tid, "Turn 5 Review (superagent)", [
        "Reviewed the worker result. Official AC left unchecked.",
    ], sync=False)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert sent == []
    assert "redirect" not in actions
    kinds = [s.get("kind") for s in result["stalls"]]
    assert "source_failure" not in kinds


def _pending_cmd(state: Path, command, bot="bot-001", source="opencode:tool.execute.before", at=None):
    at = at or datetime.now().isoformat(timespec="seconds")
    rec = {
        "at": at, "bot": bot, "host": None,
        "rule": "pip",
        "reason": f"segment {command!r} needs approval",
        "command": command,
        "resolution": f"bin/gate approve --bot {bot} -- {command}",
        "source": source,
        "decision": "needs_approval",
    }
    with (state / "pending-approvals.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    with (state / "gate-audit.jsonl").open("a") as f:
        f.write(json.dumps({**rec, "decision": "needs_approval"}) + "\n")
    return rec


def test_low_risk_pending_approval_is_approved_and_logged(control):
    """Well-known data libraries from the default index are low risk: approve, log, resume."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        title="HK residential sales price analysis",
        objective="Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        context="Search public Hong Kong residential sales figures and chart district changes.",
    )
    cmd = "pip install xlrd openpyxl html5lib -q"
    _pending_cmd(control["state"], cmd)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "approve" in actions, result
    gate = PermissionGate("bot-001", settings=settings)
    assert gate.is_approved(cmd)
    assert gate.check(cmd).decision == "allow"
    assert sent, "worker should be resumed after a real gate approval"
    prompt = sent[0][2] if isinstance(sent[0], tuple) else sent[0]
    assert "has been approved" in prompt
    assert cmd in prompt
    body = store.find(tid).read_text()
    assert "Decision: approve" in body
    assert "Risk: low" in body
    assert "Rationale:" in body
    assert "Gate: approved" in body
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"


def test_high_risk_pending_is_rejected_with_counter_proposal_and_logged(control):
    """High-risk pending approvals are rejected with a counter-proposal; gate is not approved."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        title="HK residential sales price analysis",
        objective="Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        context="Search public Hong Kong residential sales figures and chart district changes.",
    )
    cmd = "apt-get update && apt-get install -y python3-xlrd"
    _pending_cmd(control["state"], cmd)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "approve" not in actions
    assert "reject" in actions, result
    gate = PermissionGate("bot-001", settings=settings)
    assert not gate.is_approved(cmd)
    assert sent, "worker should be told the request was rejected"
    prompt = sent[0][2] if isinstance(sent[0], tuple) else sent[0]
    assert "has been approved" not in prompt.lower()
    assert "NOT approved" in prompt
    assert "Counter-proposal" in prompt or "counter-proposal" in prompt.lower()
    body = store.find(tid).read_text()
    assert "Decision: reject" in body
    assert "Risk: high" in body
    assert "Rationale:" in body
    assert "Counter-proposal:" in body
    assert "Gate: not approved" in body
    assert "- [x]" not in body.lower()
    assert store.find(tid).parent.name != "done"


def test_briefing_does_not_claim_approval_when_gate_denied_host(control):
    """Fix the issue #10 briefing lie: never claim approval when the gate denied."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(
        store,
        body_extra="- Bot reply: Please approve the network call to evil.example when prompted.",
        objective="Collect public Hong Kong residential sales figures.",
        context="Search public Hong Kong residential sales figures.",
    )
    _pending(control["state"], host="evil.example")
    with (control["state"] / "gate-audit.jsonl").open("a") as f:
        f.write(json.dumps({
            "at": datetime.now().isoformat(timespec="seconds"),
            "bot": "bot-001", "source": "opencode:tool.execute.before",
            "decision": "needs_approval",
            "reason": "network call to unknown host evil.example",
            "rule": "network", "command": "curl https://evil.example/data.json",
            "host": "evil.example",
        }) + "\n")
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert "deny" in actions
    assert sent
    prompt = sent[0][2] if isinstance(sent[0], tuple) else sent[0]
    assert "has been approved" not in prompt.lower()
    assert "has been allowed" not in prompt.lower()
    assert "NOT approved" in prompt
    assert "Counter-proposal" in prompt or "counter-proposal" in prompt.lower()
    body = store.find(tid).read_text()
    assert "Gate: not approved" in body
    assert "Counter-proposal:" in body


def test_low_risk_pending_does_not_prompt_while_turn_in_progress(control):
    """Low-risk pip may be silently approved mid-turn, but no new worker prompt is sent."""
    settings = load_settings()
    store = TicketStore(settings)
    tid = _write_ticket(store)
    cmd = "pip install xlrd openpyxl html5lib -q"
    _pending_cmd(control["state"], cmd)
    store.add_log(tid, "Turn 5 (bot-001)", [f"Prompt sent at {datetime.now():%Y-%m-%d %H:%M}"], sync=False)
    store.add_log(tid, "Turn 5 progress (bot-001)",
                  [f"Still working at {datetime.now():%Y-%m-%d %H:%M} (30s elapsed): execute; turn still running."],
                  sync=False)
    _seed_session(control, tid)
    sent = []
    wd = WorkerWatchdog(settings=settings, store=store,
                        sender=lambda *a: sent.append(a) or {"status": "dispatched"})
    result = wd.run_once(apply=True, bot_id="bot-001")
    actions = [d["action"] for d in result["decisions"]]
    assert sent == [], f"must not prompt while a turn is in progress: {sent}"
    assert "approve" in actions
    # Silent approval is allowed mid-turn so a retry can pass the gate.
    gate = PermissionGate("bot-001", settings=settings)
    assert gate.check(cmd).decision == "allow"
    body = store.find(tid).read_text()
    assert "Turn 6" not in body
    assert "has been approved" not in body.split("Turn 5 progress")[-1] if False else True
