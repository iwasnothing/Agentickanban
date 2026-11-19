"""Unit tests for the simplified '+' create-task path (description only)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from task_request import (  # noqa: E402
    JOB_WATCH_SECONDS_LOOP,
    build_superagent_prompt,
    job_watch_seconds,
    normalize_new_task,
    placeholder_title,
)


def test_normalize_description_only():
    req, errs = normalize_new_task({
        "description": "Search public Hong Kong residential sales figures and chart price-per-sqft change by district."
    })
    assert errs == []
    assert req is not None
    assert "Hong Kong residential" in req["title"]
    assert req["description"].startswith("Search public Hong Kong")
    assert req["priority"] == "medium"
    assert req["labels"] == []
    assert req["run_loop"] is True
    assert "acceptance_criteria" not in req
    assert "objective" not in req


def test_normalize_legacy_objective_alias_and_ignores_client_ac():
    req, errs = normalize_new_task({
        "objective": "Add a wave helper that returns Wave, {name}!",
        "title": "Add a wave helper",
        "acceptance_criteria": ["wave('Ada') returns Wave, Ada!"],
        "requirements": ["must not be used"],
        "run_loop": False,
        "priority": "high",
        "labels": ["python", "demo"],
    })
    assert errs == []
    assert req["title"] == "Add a wave helper"
    assert req["description"].startswith("Add a wave helper")
    assert req["run_loop"] is False
    assert req["priority"] == "high"
    assert "python" in req["labels"]
    assert "acceptance_criteria" not in req
    assert "requirements" not in req or req.get("requirements") is None


def test_normalize_rejects_short_or_missing_description():
    req, errs = normalize_new_task({"description": "too short"})
    assert req is None and any("description" in e for e in errs)
    req, errs = normalize_new_task({"title": "Only a title here"})
    assert req is None and any("description" in e for e in errs)


def test_placeholder_title_truncates():
    long = "A" * 200
    assert placeholder_title(long) == "A" * 120
    assert placeholder_title("  hi\nmore") == "New task"  # first line too short after strip? "hi" is 2 chars
    assert placeholder_title("Chart HK prices by district") == "Chart HK prices by district"


def test_build_prompt_asks_superagent_to_expand_and_assign():
    req, _ = normalize_new_task({
        "description": "Search public Hong Kong residential sales figures and visualize the ranking."
    })
    prompt = build_superagent_prompt(req, "webui-request:abc123")
    assert "high-level" in prompt.lower() or "high_level_description" in prompt
    assert "webui-request:abc123" in prompt
    assert "ticket_create" in prompt
    assert "objective" in prompt.lower()
    assert "requirements" in prompt.lower()
    assert "acceptance criteria" in prompt.lower()
    assert "exactly the fields in the JSON" not in prompt
    assert "bot-001" in prompt
    assert "NEVER tick" in prompt or "never tick" in prompt.lower()
    assert "bot_send_prompt" in prompt
    assert "run_loop" not in prompt or "Kanban loop" in prompt
    # user AC must not be pre-filled into the ticket JSON
    assert "acceptance_criteria" not in prompt.split("Request JSON:")[1].split("```")[1]


def test_job_watch_covers_max_wait():
    assert job_watch_seconds(True) == JOB_WATCH_SECONDS_LOOP
    assert JOB_WATCH_SECONDS_LOOP >= 3600 + 20 * 60
    assert job_watch_seconds(False) >= 15 * 60


def test_new_task_form_is_description_only():
    html = (ROOT / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="fdesc"' in html
    assert "High-level task description" in html
    assert 'id="fac"' not in html
    assert 'id="fobj"' not in html
    assert 'id="floop"' not in html
    assert 'id="ftitle"' not in html
    form = html.split('<form class="mbox" id="form">')[1].split("</form>")[0]
    assert "acceptance_criteria" not in form
    assert "description:$('#fdesc').value" in html
