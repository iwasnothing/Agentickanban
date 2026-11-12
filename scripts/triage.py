"""Rule-based triage (superagent/config/triage_rules.yaml). The LLM superagent can use
this as a suggestion and override it; it is also the fallback when no model is available."""
from __future__ import annotations

import json
import re

from scripts.ticket_sync.store import TicketStore

ACTIVE = ("to-do", "planning", "in-progress")


def _bot_keywords(store: TicketStore, bot_id: str) -> set[str]:
    path = store.s.bots_dir / bot_id / "SKILL.md"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8").lower()
    m = re.search(r"^keywords:\s*(.+)$", text, re.M)
    kws = {k.strip() for k in m.group(1).split(",")} if m else set()
    return {k for k in kws if k}


def _bot_mcp(store: TicketStore, bot_id: str) -> set[str]:
    path = store.s.bots_dir / bot_id / "mcp.json"
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    return set((data.get("mcpServers") or {}).keys()) | set(data.get("provides") or [])


def triage(store: TicketStore, ticket_id: str, apply: bool = False) -> dict:
    rules = store.s.triage_rules()
    w = rules.get("weights", {})
    t = store.get(ticket_id)
    meta, secs = t["metadata"], t["sections"]
    text = " ".join([meta.get("title", ""), secs.get("Objective", ""), secs.get("Context", ""),
                     secs.get("Acceptance Criteria", ""), " ".join(meta.get("labels") or [])]).lower()
    labels = [str(l).lower() for l in meta.get("labels") or []]
    needed_mcp = {l.split("/", 1)[1] for l in labels if l.startswith("mcp/")}
    scores = []
    for bot_id, info in store.s.registry().items():
        if not info.get("enabled", True):
            continue
        kws = _bot_keywords(store, bot_id)
        hits = sorted(k for k in kws if re.search(rf"\b{re.escape(k)}\b", text))
        score = len(hits) * w.get("skill_keyword_hit", 3)
        boosts = [l for l in labels if bot_id in (rules.get("label_boosts", {}) or {}).get(l, [])]
        score += len(boosts) * w.get("label_match", 5)
        missing = sorted(needed_mcp - _bot_mcp(store, bot_id))
        if missing:
            score += w.get("mcp_requirement_missing", -100)
        load = sum(1 for c in ACTIVE for x in store.list(status=c) if x.get("assignee_bot") == bot_id)
        score -= load * w.get("open_ticket_penalty", 1)
        scores.append({"bot_id": bot_id, "score": score, "keyword_hits": hits, "label_boosts": boosts,
                       "missing_mcp": missing, "open_tickets": load})
    scores.sort(key=lambda s: (-s["score"], s["open_tickets"]))
    best = scores[0] if scores else None
    chosen, reason = None, ""
    if best and best["score"] >= rules.get("min_score_to_assign", 0) and not best["missing_mcp"]:
        chosen, reason = best["bot_id"], f"highest score {best['score']} (hits: {', '.join(best['keyword_hits']) or 'none'})"
    elif rules.get("default_bot") and any(s["bot_id"] == rules["default_bot"] and not s["missing_mcp"] for s in scores):
        chosen, reason = rules["default_bot"], "no bot scored above the threshold; using default_bot"
    triggers = [p for p in rules.get("planning_triggers", []) if p.lower() in text]
    column = "planning" if triggers else rules.get("after_assign_move_to", "to-do")
    result = {"ticket_id": ticket_id, "suggested_bot": chosen, "suggested_column": column if chosen else "backlog",
              "reason": reason or "no eligible bot", "planning_triggers": triggers, "scores": scores, "applied": False}
    if apply and chosen:
        store.assign(ticket_id, chosen)
        store.move(ticket_id, column)
        store.add_log(ticket_id, "Triage (superagent)", [f"Assigned to {chosen}: {reason}", f"Moved to {column}"])
        result["applied"] = True
    return result
