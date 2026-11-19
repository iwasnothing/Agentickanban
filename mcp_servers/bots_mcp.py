"""Kanban bots MCP server (stdio) for the DeepSeek Superagent — the blueprint's
"OpenMausBot MCP server" (bot.* tools, MCP-legal names with underscores).

Each Kanban bot maps to an OpenMausBot bot (bots/registry.yaml) working in its own
git worktree of the project repo (branch bots/<bot-id>). Sessions are OpenMausBot
conversations whose working folder is that worktree. Every command — whether the
superagent asks (bot_run_command / bot_run_tests) or the coding agent runs it inside
OpenMausBot (opencode tool.execute.before plugin) — passes the same permission gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from scripts.bots.core import BotManager  # noqa: E402

mcp = FastMCP("kanban-bots", instructions=(
    "Dispatch work to Kanban bots (OpenMausBot workers in git worktrees). Typical turn: bot_start_session -> "
    "bot_send_prompt (waits for the turn) -> bot_get_last_turn_diff -> bot_run_tests -> review -> "
    "bot_commit_turn. Commands pass a permission gate (allow / deny / needs_approval)."))
_mgr: BotManager | None = None


def mgr() -> BotManager:
    global _mgr
    if _mgr is None:
        _mgr = BotManager()
    return _mgr


@mcp.tool()
def bot_list() -> list[dict]:
    """List Kanban bots with their OpenMausBot mapping, live activity, workspace and open tickets."""
    return mgr().list()


@mcp.tool()
def bot_get_config(bot_id: str) -> dict:
    """A bot's AGENTS.md (persona), SKILL.md (skills), mcp.json, sandbox config and registry entry."""
    return mgr().get_config(bot_id)


@mcp.tool()
def bot_start_session(bot_id: str, ticket_id: str) -> dict:
    """Start a work session for a ticket: ensures the worktree, copies the ticket + attachments into
    <workspace>/.ticket/, opens an OpenMausBot conversation working in the worktree. Returns session_id."""
    return mgr().start_session(bot_id, ticket_id)


@mcp.tool()
def bot_send_prompt(bot_id: str, session_id: str, prompt: str, wait: bool = True, timeout_seconds: int | None = None) -> dict:
    """Send a prompt (one action turn) to the bot. With wait=true (default) blocks until the turn ends and
    returns status (done | failed | needs-user | timed-out), the bot's reply and a tool-activity digest.
    timeout_seconds defaults to openmausbot.default_wait_seconds (30 minutes) and may be extended up to
    max_wait_seconds while the bot is still using tools. The ticket log records in-progress tool activity
    instead of a blank reply if the wait ends before a finished text message arrives."""
    return mgr().send_prompt(bot_id, session_id, prompt, wait=wait, timeout_s=timeout_seconds)


@mcp.tool()
def bot_get_session_status(bot_id: str, session_id: str) -> dict:
    """Session bookkeeping (turn, commits) plus the bot's latest reply — use after send with wait=false."""
    return mgr().get_session_status(bot_id, session_id)


@mcp.tool()
def bot_run_command(bot_id: str, cmd: str, session_id: str | None = None) -> dict:
    """Run a shell command in the bot's workspace through the permission gate. Returns stdout/stderr/exit code,
    or status blocked / needs_approval with the gate's reason."""
    return mgr().run_command(bot_id, session_id, cmd)


@mcp.tool()
def bot_get_workspace_tree(bot_id: str) -> dict:
    """Files in the bot's worktree (tracked + untracked), branch, HEAD and git status."""
    return mgr().get_workspace_tree(bot_id)


@mcp.tool()
def bot_read_file(bot_id: str, path: str) -> dict:
    """Read a file inside the bot's workspace (path relative to the worktree)."""
    return mgr().read_file(bot_id, path)


@mcp.tool()
def bot_write_file(bot_id: str, path: str, content: str) -> dict:
    """Write a file inside the bot's workspace (no .git/, no gate plugin)."""
    return mgr().write_file(bot_id, path, content)


@mcp.tool()
def bot_get_last_turn_diff(bot_id: str, session_id: str) -> dict:
    """Diff of the current turn (turn start -> working tree, new files included); after a commit, the last
    committed turn's diff."""
    return mgr().get_last_turn_diff(bot_id, session_id)


@mcp.tool()
def bot_commit_turn(bot_id: str, session_id: str, message: str | None = None) -> dict:
    """End-of-turn commit on branch bots/<bot-id> ("bot(<id>): turn N for ticket <ticket>") and push if the
    worktree has a remote."""
    return mgr().commit_turn(bot_id, session_id, message)


@mcp.tool()
def bot_run_tests(bot_id: str, session_id: str | None = None, test_cmd: str | None = None) -> dict:
    """Run tests in the bot's workspace through the permission gate (default: the bot's default_test_cmd)."""
    return mgr().run_tests(bot_id, session_id, test_cmd)


if __name__ == "__main__":
    mcp.run()


@mcp.tool()
def bot_watchdog_check(bot_id: str | None = None, apply: bool = True) -> dict:
    """Inspect every configured worker (or one bot) for stalls — host-permission waits,
    other permission-gate needs_approval, or hung/no-progress turns — and optionally
    apply Superagent's decision (allow official/safe hosts and resume, deny inappropriate
    hosts and fail clearly, or record a reasoned note). Never ticks official AC boxes
    or marks a ticket Done just to clear a stall. Interval and safe suffixes come from
    superagent/config/superagent.yaml (watchdog)."""
    from scripts.bots.watchdog import WorkerWatchdog
    return WorkerWatchdog().run_once(apply=apply, bot_id=bot_id)
