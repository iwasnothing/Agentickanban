import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _tools(script):
    p = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_servers" / script)], env=dict(os.environ))
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            extra = None
            if script == "ticket_mcp.py":
                res = await s.call_tool("ticket_create", {"title": "From MCP", "objective": "x", "acceptance_criteria": ["y"]})
                extra = json.loads(res.content[0].text)
                prompts = [p.name for p in (await s.list_prompts()).prompts]
                extra["prompts"] = prompts
            return names, extra


def test_ticket_mcp(control):
    names, created = asyncio.run(_tools("ticket_mcp.py"))
    assert {"ticket_create", "ticket_get", "ticket_update", "ticket_move", "ticket_assign",
            "ticket_add_review_note", "ticket_list"} <= set(names)
    assert created["ticket_id"].endswith("from-mcp")
    assert {"triage_prompt", "review_prompt", "gap_analysis_prompt"} <= set(created["prompts"])


def test_bots_mcp(control):
    names, _ = asyncio.run(_tools("bots_mcp.py"))
    assert {"bot_list", "bot_get_config", "bot_start_session", "bot_send_prompt", "bot_run_command",
            "bot_get_workspace_tree", "bot_read_file", "bot_write_file", "bot_get_last_turn_diff",
            "bot_commit_turn", "bot_run_tests"} <= set(names)
