"""Ticket MCP server (stdio) for the DeepSeek Superagent.

Tools (blueprint `ticket.*`, MCP-legal names with underscores):
  ticket_create, ticket_get, ticket_update, ticket_move, ticket_assign,
  ticket_add_review_note, ticket_list, ticket_triage, ticket_sync_github
Prompts: triage, review, gap_analysis (from superagent/prompts/).

Markdown files under tickets/<column>/ are the source of truth; every mutation
runs scripts/ticket_sync/github_sync.py (dry-run without GITHUB_TOKEN).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from scripts.config import load_settings  # noqa: E402
from scripts.ticket_sync.github_sync import sync_ticket  # noqa: E402
from scripts.ticket_sync.store import TicketStore  # noqa: E402
from scripts.triage import triage  # noqa: E402

Column = Literal["backlog", "to-do", "planning", "in-progress", "in-review", "done"]
Priority = Literal["high", "medium", "low"]

mcp = FastMCP("kanban-tickets", instructions=(
    "Kanban ticket backend. Tickets are Markdown files; the folder (column) is canonical. "
    "Columns: backlog, to-do, planning, in-progress, in-review, done. Use ticket_triage for a rule-based bot "
    "suggestion, ticket_assign + ticket_move to apply. GitHub Issues/Projects sync runs after every change."))
_store: TicketStore | None = None


def store() -> TicketStore:
    global _store
    if _store is None:
        _store = TicketStore(load_settings())
    return _store


@mcp.tool()
def ticket_create(title: str, objective: str, acceptance_criteria: list[str] | None = None,
                  context: list[str] | None = None, attachments: list[str] | None = None,
                  priority: Priority = "medium", labels: list[str] | None = None,
                  status: Column = "backlog") -> dict:
    """Create a Markdown ticket (default column: backlog). attachments are file paths on this machine
    (copied into attachments/<ticket-id>/) or URLs. Returns ticket_id."""
    return store().create(title=title, objective=objective, context=context, acceptance_criteria=acceptance_criteria,
                          attachments=attachments, priority=priority, labels=labels, status=status)


@mcp.tool()
def ticket_get(ticket_id: str) -> dict:
    """Get a ticket: metadata (front-matter), sections, parsed acceptance criteria and raw markdown."""
    return store().get(ticket_id)


@mcp.tool()
def ticket_update(ticket_id: str, patch: dict) -> dict:
    """Patch a ticket. Allowed keys: title, priority, labels, assignee_bot, objective, context,
    acceptance_criteria (list -> checkboxes), attachments, final_review, sections ({heading: markdown}).
    Status cannot be patched: use ticket_move."""
    return store().update(ticket_id, patch)


@mcp.tool()
def ticket_move(ticket_id: str, to_column: Column) -> dict:
    """Move a ticket to another column (moves the file; front-matter status mirrors it).
    Moving to done closes the GitHub issue."""
    return store().move(ticket_id, to_column)


@mcp.tool()
def ticket_assign(ticket_id: str, bot_id: str) -> dict:
    """Assign a ticket to a registered bot (bots/registry.yaml), e.g. bot-001."""
    return store().assign(ticket_id, bot_id)


@mcp.tool()
def ticket_add_review_note(ticket_id: str, note: str, turn: int | None = None, final: bool = False) -> dict:
    """Append a superagent review note to the Execution Log as '## Turn N Review (superagent)'
    (turn defaults to the latest bot turn). final=true writes the Final Review section instead."""
    return store().add_review_note(ticket_id, note, turn=turn, final=final)


@mcp.tool()
def ticket_list(status: Column | None = None, assignee_bot: str | None = None) -> list[dict]:
    """List tickets, optionally filtered by column and/or assignee bot."""
    return store().list(status=status, assignee_bot=assignee_bot)


@mcp.tool()
def ticket_triage(ticket_id: str, apply: bool = False) -> dict:
    """Rule-based triage (superagent/config/triage_rules.yaml): scores bots by SKILL.md keywords, labels,
    MCP requirements and workload. apply=true assigns the suggested bot and moves the ticket."""
    return triage(store(), ticket_id, apply=apply)


@mcp.tool()
def ticket_sync_github(ticket_id: str) -> dict:
    """Force a GitHub Issue + Projects sync for one ticket (dry-run when GITHUB_TOKEN is not set)."""
    return sync_ticket(ticket_id, event="manual", store=store())


def _prompt(name: str) -> str:
    return (store().s.root / "superagent" / "prompts" / f"{name}.md").read_text(encoding="utf-8")


@mcp.prompt()
def triage_prompt(ticket_id: str) -> str:
    """Superagent triage prompt for one ticket."""
    return _prompt("triage").replace("{ticket_id}", ticket_id)


@mcp.prompt()
def review_prompt(ticket_id: str, bot_id: str, session_id: str, turn: str = "1") -> str:
    """Superagent review prompt for one bot turn."""
    return (_prompt("review").replace("{ticket_id}", ticket_id).replace("{bot_id}", bot_id)
            .replace("{session_id}", session_id).replace("{turn}", turn))


@mcp.prompt()
def gap_analysis_prompt() -> str:
    """Gap analysis instructions (acceptance criteria vs diff + tests)."""
    return _prompt("gap_analysis")


if __name__ == "__main__":
    mcp.run()
