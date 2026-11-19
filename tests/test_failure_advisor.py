"""Unit tests for the Superagent source-failure advisor (no live worker)."""
from scripts.bots.failure_advisor import (
    advise,
    detect_source_failure,
    extract_intent,
    last_worker_result,
    prompt_in_flight,
    result_already_handled,
    Intent,
    SourceFailure,
)


def test_detect_missing_and_404():
    miss = detect_source_failure(
        "Bot reply: I cannot find the required dataset. The source URL returned 404."
    )
    assert miss is not None
    assert miss.subtype in {"missing_source", "http_404"}
    assert "404" in miss.evidence or "cannot find" in miss.evidence.lower()


def test_detect_paid_walled_takes_priority():
    paid = detect_source_failure(
        "The dataset is not publicly available. The only source is a paid subscription "
        "behind a login-walled portal and requires a paid sign-in."
    )
    assert paid is not None
    assert paid.subtype == "paid_walled"


def test_extract_intent_uses_high_level_not_later_source():
    body = """# Objective
Collect public Hong Kong residential sales figures and compute price-per-sq-ft change by district.

# Context
Requested via the Agentic Kanban web UI. This is the user's high-level description only — Superagent will expand it.
Search public Hong Kong residential sales figures and chart district changes.

# Execution Log
## Turn 5 result (bot-001)
- Bot reply: EPRC commercial feed is missing. I will invent a private API instead.
"""
    intent = extract_intent(body, title="HK residential sales")
    assert "public Hong Kong residential" in intent.summary
    assert "invent a private API" not in intent.summary
    assert "EPRC" not in intent.summary


def test_advise_hk_public_intent_suggests_official_sources():
    intent = Intent(
        title="HK residential sales",
        objective="Collect public Hong Kong residential sales figures",
        high_level="Search public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
        summary="Search public Hong Kong residential sales figures and compute price-per-sq-ft change by district.",
    )
    alt = advise(intent, SourceFailure("missing_source", "EPRC is not publicly available"))
    assert alt.kind == "public_official"
    joined = " ".join(alt.sources).lower() + alt.suggestion.lower()
    assert "data.gov.hk" in joined or "rvd.gov.hk" in joined
    assert "leftover" in alt.suggestion.lower()


def test_advise_paid_is_not_auto_approved():
    intent = Intent(summary="public HK prices", title="HK prices", objective="public HK prices")
    alt = advise(intent, SourceFailure("paid_walled", "requires a paid subscription"))
    assert alt.kind == "paid_walled"
    assert "not auto-approved" in alt.reason.lower() or "will not auto-buy" in alt.suggestion.lower()


def test_last_result_and_in_flight_helpers():
    body = """# Execution Log
## Turn 4 result (bot-001)
- missing source
## Turn 5 (bot-001) — progress
- still working
"""
    assert prompt_in_flight(body) is True
    result = last_worker_result(body)
    assert result and "missing source" in result["body"]
    # A watchdog note after the result marks it handled
    handled = body + "\n## Watchdog (superagent)\n- Stall: source_failure on bot-001\n"
    assert result_already_handled(handled) is True
