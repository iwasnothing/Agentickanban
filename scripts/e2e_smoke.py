"""End-to-end smoke run through the two MCP servers (stand-in for the superagent's model).

Flow: ticket_create -> ticket_triage(apply) -> ticket_move in-progress -> bot_start_session ->
bot_send_prompt (real OpenMausBot turn in the bot-001 worktree) -> bot_get_last_turn_diff ->
bot_run_tests -> gap check -> bot_commit_turn -> ticket_add_review_note -> ticket_move in-review -> done
-> ticket_sync_github (dry-run without GITHUB_TOKEN).

usage: .venv/bin/python scripts/e2e_smoke.py [--timeout 600]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def log(step: str, data) -> None:
    text = json.dumps(data, indent=2, default=str) if not isinstance(data, str) else data
    print(f"\n=== {step} ===\n{text[:3000]}", flush=True)


async def call(s: ClientSession, tool: str, args: dict):
    res = await s.call_tool(tool, args)
    text = "\n".join(c.text for c in res.content if hasattr(c, "text"))
    if res.isError:
        raise RuntimeError(f"{tool} failed: {text}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = text
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


async def main(timeout: int) -> int:
    env = {k: v for k, v in os.environ.items()}
    py = sys.executable
    summary: dict = {"started_at": datetime.now().isoformat(timespec="seconds")}
    async with AsyncExitStack() as stack:
        sessions = {}
        for name, script in (("tickets", "ticket_mcp.py"), ("bots", "bots_mcp.py")):
            r, w = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=py, args=[str(ROOT / "mcp_servers" / script)], env=env)))
            s = await stack.enter_async_context(ClientSession(r, w))
            await s.initialize()
            sessions[name] = s
        T, B = sessions["tickets"], sessions["bots"]

        bots = await call(B, "bot_list", {})
        log("bot_list", bots)

        created = await call(T, "ticket_create", {
            "title": "Add farewell function",
            "objective": "Add a farewell(name) function next to greet() in src/demo/greet.py.",
            "context": ["Code: src/demo/greet.py", "Tests: tests/test_greet.py (unittest)"],
            "acceptance_criteria": ['farewell("Ada") returns "Goodbye, Ada!"',
                                    "A unittest covers farewell", "All tests pass"],
            "priority": "medium", "labels": ["python", "backend"]})
        tid = created["ticket_id"]
        summary["ticket_id"] = tid
        log("ticket_create", created)

        tri = await call(T, "ticket_triage", {"ticket_id": tid, "apply": True})
        log("ticket_triage (apply)", {k: tri[k] for k in ("suggested_bot", "suggested_column", "reason", "applied")})
        bot_id = tri["suggested_bot"]
        assert bot_id == "bot-001", tri

        log("ticket_move -> in-progress", await call(T, "ticket_move", {"ticket_id": tid, "to_column": "in-progress"}))

        sess = await call(B, "bot_start_session", {"bot_id": bot_id, "ticket_id": tid})
        log("bot_start_session", sess)
        sid = sess["session_id"]
        summary["session_id"] = sid

        prompt = (
            f"Ticket {tid}: Add farewell function.\n\n"
            "Objective: add `farewell(name: str) -> str` to src/demo/greet.py returning f\"Goodbye, {name}!\".\n"
            "Acceptance criteria:\n- farewell(\"Ada\") returns \"Goodbye, Ada!\"\n- a unittest in tests/test_greet.py covers farewell\n"
            "- all tests pass\n\n"
            "Constraints: edit only src/demo/greet.py and tests/test_greet.py; do not commit; no package installs.\n"
            "Required tests: run `python3 -m unittest discover -s tests -v`.\n"
            "Finally, as a permission-gate check, try to run `pip install requests` exactly once and report whether it "
            "was allowed or blocked (do not retry or work around it).\n"
            "Reply briefly: files changed, test result, gate result.")
        sent = await call(B, "bot_send_prompt", {"bot_id": bot_id, "session_id": sid, "prompt": prompt,
                                                 "wait": True, "timeout_seconds": timeout})
        log("bot_send_prompt", sent)
        summary["bot_turn_status"] = sent.get("status")
        summary["bot_reply"] = sent.get("reply")

        diff = await call(B, "bot_get_last_turn_diff", {"bot_id": bot_id, "session_id": sid})
        log("bot_get_last_turn_diff", {"files_changed": diff["files_changed"], "diff": diff["diff"][:2500]})
        summary["files_changed"] = diff["files_changed"]

        tests = await call(B, "bot_run_tests", {"bot_id": bot_id, "session_id": sid})
        log("bot_run_tests", {k: tests.get(k) for k in ("test_cmd", "status", "passed", "exit_code")} |
            {"output": (tests.get("stderr") or "")[-1500:]})
        summary["tests_passed"] = tests.get("passed")

        # deterministic gap analysis (what the review prompt asks the LLM to do)
        d = diff["diff"]
        checks = {
            'farewell("Ada") returns "Goodbye, Ada!"': "def farewell" in d and "Goodbye" in d,
            "A unittest covers farewell": "tests/test_greet.py" in diff["files_changed"] and "farewell" in d,
            "All tests pass": bool(tests.get("passed")),
        }
        gaps = [k for k, ok in checks.items() if not ok]
        summary["gaps"] = gaps
        log("gap analysis", checks)

        commit = await call(B, "bot_commit_turn", {"bot_id": bot_id, "session_id": sid})
        log("bot_commit_turn", commit)
        summary["commit"] = commit

        note = "\n".join([f"Files changed: {', '.join(diff['files_changed']) or 'none'}",
                          f"Tests: {'pass' if tests.get('passed') else 'FAIL'} ({tests.get('test_cmd')})",
                          *[f"[{'x' if ok else ' '}] {k}" for k, ok in checks.items()],
                          f"Gaps: {', '.join(gaps) if gaps else 'none'}",
                          f"Action: {'approve' if not gaps else 'request changes'}",
                          f"Commit: {commit.get('head')} ({'pushed' if commit.get('pushed') else commit.get('push', 'not pushed')})"])
        log("ticket_add_review_note", await call(T, "ticket_add_review_note", {"ticket_id": tid, "note": note}))

        if not gaps:
            await call(T, "ticket_update", {"ticket_id": tid, "patch": {"acceptance_criteria": [f"[x] {k}" for k in checks]}})
            log("ticket_move -> in-review", await call(T, "ticket_move", {"ticket_id": tid, "to_column": "in-review"}))
            await call(T, "ticket_add_review_note", {"ticket_id": tid, "final": True,
                                                     "note": f"- Approved by superagent (e2e smoke) at {datetime.now():%Y-%m-%d %H:%M}\n"
                                                             f"- Commit {commit.get('head')} on bots/{bot_id}"})
            log("ticket_move -> done", await call(T, "ticket_move", {"ticket_id": tid, "to_column": "done"}))
        else:
            log("result", "gaps remain: ticket left in-progress for another iteration")

        sync = await call(T, "ticket_sync_github", {"ticket_id": tid})
        log("ticket_sync_github", sync)
        summary["github_sync_mode"] = sync.get("mode")
        final = await call(T, "ticket_get", {"ticket_id": tid})
        summary["final_status"] = final["metadata"]["status"]
        log("final ticket", final["markdown"])
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (ROOT / ".state" / "e2e-last-run.json").write_text(json.dumps(summary, indent=2, default=str))
    log("SUMMARY", summary)
    ok = summary["final_status"] == "done" and not summary["gaps"]
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600)
    raise SystemExit(asyncio.run(main(ap.parse_args().timeout)))
