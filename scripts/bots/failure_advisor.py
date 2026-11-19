"""Interpret a finished worker-turn failure and suggest an alternative.

The advisor is a local, testable Superagent decision helper used by the
periodic worker watchdog. It does not start a new Superagent session, invent
figures, copy leftover artifacts, tick official acceptance criteria, or
auto-buy / log in to paid or login-walled sources.

Intent is taken from the ticket Objective, Context, and the user's original
high-level (+) description — never from a later invented source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from scripts.ticket_sync import md_utils as md

# Finished worker-turn headings look like: "## Turn 5 result (bot-001)"
_WORKER_RESULT_RE = re.compile(r"^## (Turn \d+ result \([^)]+\))\s*$", re.M)
_HEADING_RE = re.compile(r"^## (.+)\s*$", re.M)
_HIGH_LEVEL_RE = re.compile(
    r"(?:high-level (?:prompt|description|request)(?: received)?:|"
    r"user(?:'s)? high-level (?:description|request):)\s*(.+)",
    re.I,
)

_MISSING_RES = [
    re.compile(p, re.I)
    for p in (
        r"not (?:publicly )?available",
        r"cannot (?:find|locate|access|retrieve)",
        r"could not (?:find|locate|access|retrieve)",
        r"\b404\b",
        r"page not found",
        r"(?:data )?source (?:is |was )?(?:unavailable|missing|not found|does not exist)",
        r"(?:dataset|data set) (?:is |was )?(?:unavailable|missing|not found|does not exist)",
        r"no (?:public |official )?(?:dataset|data source|open data)",
        r"does not (?:exist|publish)",
        r"not found (?:as|in|on|from)",
        r"wrong (?:url|source|link)",
        r"link (?:is )?(?:dead|broken|invalid)",
        r"requested (?:dataset|data|source) (?:is )?(?:unavailable|missing)",
    )
]

_PAID_RES = [
    re.compile(p, re.I)
    for p in (
        r"\bpaywall",
        r"login[- ]walled",
        r"behind (?:a )?(?:login|paywall|subscription)",
        r"requires? (?:a )?(?:paid|payment|subscription|login|sign[- ]?in|account)",
        r"paid (?:subscription|access|licence|license|source|dataset|data)",
        r"commercial (?:subscription|licence|license|dataset|feed)",
        r"not available without (?:payment|a subscription|logging in|an account)",
        r"must (?:pay|subscribe|log ?in|sign[- ]?in)",
        r"subscription[- ]only",
        r"pay[- ]to[- ]access",
        r"only (?:available|option|source).{0,60}(?:paid|subscription|login|paywall)",
    )
]

# Official public substitutes for a public HK residential-price intent.
OFFICIAL_HK_PRICE_SOURCES = [
    "Rating and Valuation Department (rvd.gov.hk) private domestic price indices and HK Property Review tables",
    "data.gov.hk official open datasets for private domestic prices",
    "Land Registry open monthly statistics (landreg.gov.hk) when published as open data",
]


@dataclass
class SourceFailure:
    subtype: str  # missing_source | http_404 | paid_walled
    evidence: str
    last_activity: str | None = None


@dataclass
class Intent:
    title: str = ""
    objective: str = ""
    context: str = ""
    high_level: str = ""
    summary: str = ""


@dataclass
class Alternative:
    kind: str  # public_official | paid_walled | unclear
    suggestion: str
    reason: str
    sources: list[str] = field(default_factory=list)


def _execution_log(body: str) -> str:
    idx = body.find("# Execution Log")
    return body[idx:] if idx >= 0 else body


def last_worker_result(body: str) -> dict | None:
    """Return the last finished worker-turn result (heading + body span).

    Superagent reviews and Watchdog notes are ignored. Detection always uses
    this last result, never a ticket-wide prose scan.
    """
    log = _execution_log(body)
    matches = [
        m for m in _WORKER_RESULT_RE.finditer(log)
        if "superagent" not in m.group(1).lower() and "watchdog" not in m.group(1).lower()
    ]
    if not matches:
        return None
    m = matches[-1]
    nxt = _HEADING_RE.search(log, m.end())
    end = nxt.start() if nxt else len(log)
    return {
        "heading": m.group(1),
        "body": log[m.end():end].strip(),
        "start": m.start(),
        "end": end,
        "log": log,
    }


def prompt_in_flight(body: str) -> bool:
    """True when the latest Turn heading is a sent prompt or in-progress turn.

    A finished result or Superagent review as the last Turn heading is not in flight.
    """
    log = _execution_log(body)
    headings = [m.group(1) for m in _HEADING_RE.finditer(log) if m.group(1).startswith("Turn ")]
    if not headings:
        return False
    last = headings[-1]
    if re.search(r"\b(result|review)\b", last, re.I):
        return False
    return True


def result_already_handled(body: str, result: dict | None = None) -> bool:
    """True if a Watchdog source_failure note or a Superagent review already followed this result."""
    result = result or last_worker_result(body)
    if not result:
        return False
    log = result.get("log") or _execution_log(body)
    after = log[result["end"]:]
    if re.search(r"Stall:\s*source_failure", after):
        return True
    if re.search(r"^## Turn \d+ Review \(superagent\)", after, re.M):
        return True
    return False


def _first_match(patterns: list[re.Pattern], text: str) -> re.Match | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m
    return None


def _snippet(text: str, match: re.Match | None, width: int = 280) -> str:
    if not text:
        return ""
    if not match:
        return " ".join(text.split())[:width]
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 160)
    return " ".join(text[start:end].split())[:width]


def detect_source_failure(result_body: str) -> SourceFailure | None:
    """Classify a finished worker-turn result as a source/data failure, or None."""
    text = result_body or ""
    paid = _first_match(_PAID_RES, text)
    missing = _first_match(_MISSING_RES, text)
    http_404 = bool(re.search(r"\b404\b|page not found", text, re.I))
    if paid:
        return SourceFailure("paid_walled", _snippet(text, paid))
    if missing:
        subtype = "http_404" if http_404 else "missing_source"
        return SourceFailure(subtype, _snippet(text, missing))
    return None


def extract_intent(body: str, title: str | None = None) -> Intent:
    """Read the user's original high-level intent from Objective / Context / + description."""
    secs = md.sections(body or "")
    objective = (secs.get("Objective") or "").strip()
    context = (secs.get("Context") or "").strip()
    high = ""
    m = _HIGH_LEVEL_RE.search(body or "")
    if m:
        high = m.group(1).strip().strip("-").strip()
    if not high:
        ctx = re.sub(r"Requested via the Agentic Kanban web UI\.?", "", context, flags=re.I)
        ctx = re.sub(
            r"This is the user's high-level description only[^.]*\.",
            "",
            ctx,
            flags=re.I,
        )
        high = " ".join(ctx.split()).strip(" -")
    summary = high or objective or (title or "")
    summary = " ".join(summary.split())
    return Intent(
        title=(title or "").strip(),
        objective=objective,
        context=context,
        high_level=high,
        summary=summary,
    )


def _looks_like_hk_public_prices(text: str) -> bool:
    t = text or ""
    has_place = re.search(r"hong\s*kong|\bhk\b", t, re.I)
    has_topic = re.search(r"resident|propert|housing|price|sales|sq\.?\s*ft|square\s*foot", t, re.I)
    return bool(has_place and has_topic)


def advise(intent: Intent, failure: SourceFailure) -> Alternative:
    """Suggest an alternative from high-level intent. Never auto-approve paid/walled sources."""
    text = " ".join(
        part for part in (intent.high_level, intent.objective, intent.context, intent.title) if part
    )
    if failure.subtype == "paid_walled":
        return Alternative(
            kind="paid_walled",
            suggestion=(
                "The worker reported that the remaining source is paid or login-walled. "
                "Superagent will not auto-buy, subscribe, or log in. Official acceptance "
                "criteria stay unchecked for a human. If a clearly public official substitute "
                "exists for the original high-level intent, a human can brief the worker to use it."
            ),
            reason="paid or login-walled source is not auto-approved",
            sources=[],
        )
    if _looks_like_hk_public_prices(text):
        listed = "; ".join(OFFICIAL_HK_PRICE_SOURCES)
        return Alternative(
            kind="public_official",
            suggestion=(
                "The user's high-level intent is public Hong Kong residential price-per-area "
                "change by district (or similar official public figures), not a particular "
                f"commercial feed. Use official public substitutes: {listed}. "
                "Transaction-level commercial feeds (for example EPRC) may not be published as "
                "open data. If only coarser official series exist, use those, state the limitation "
                "clearly, and do not invent district figures or copy earlier leftover files and "
                "present them as new official results. Do not shrink or tick official "
                "acceptance-criteria checkboxes."
            ),
            reason="official public HK government open-data substitutes match the original intent",
            sources=list(OFFICIAL_HK_PRICE_SOURCES),
        )
    if re.search(r"\b(public|official|open data|open-data|government)\b", text, re.I):
        summary = intent.summary[:400] or "the original ticket objective"
        return Alternative(
            kind="public_official",
            suggestion=(
                f"The user's high-level intent is: {summary}. "
                "The requested source is unavailable. Switch to an official public / open-data "
                "substitute in the same domain and jurisdiction if one exists. Document coverage "
                "gaps. Do not invent data, do not copy leftover artifacts and call them fresh, "
                "and do not tick or shrink official acceptance-criteria checkboxes. Do not use "
                "paid or login-walled sources."
            ),
            reason="intent asks for public/official data; suggest an official open substitute",
            sources=[],
        )
    summary = intent.summary[:400] or "the original ticket objective"
    return Alternative(
        kind="unclear",
        suggestion=(
            f"The worker could not obtain a required source. High-level intent: {summary}. "
            "No clearly public/safe substitute was identified automatically. Recorded for a "
            "human; official acceptance criteria left unchecked. Do not invent data."
        ),
        reason="no clearly public/safe alternative identified",
        sources=[],
    )
