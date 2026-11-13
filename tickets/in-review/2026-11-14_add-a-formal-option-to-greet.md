---
id: 2026-11-14_add-a-formal-option-to-greet
title: Add a formal option to greet
status: in-review
assignee_bot: bot-001
github_issue: 4
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhIfwLI
priority: medium
labels: [python, backend]
created_at: '2026-11-14'
updated_at: '2026-11-14'
---

# Objective
In the bot-001 worktree (/workspace/aibots/workspaces/bot-001, branch bots/bot-001), add greet(..., formal=False) in src/demo/greet.py. greet("Ada", formal=True) must equal "Good day, Ada." (period, not exclamation). Default formal=False so greet("Ada") still equals "Hello, Ada!". formal must also work with shout: greet("Ada", formal=True, shout=True) must equal "GOOD DAY, ADA.". Keep farewell() and the existing shout behavior unchanged. Add or update unittest coverage in tests/test_greet.py. Implementation and tests MUST run on the Simular box remote VM via OpenMausBot's enabled MCP server named box (https://mcp.box.simular.ai/mcp). After tests pass on the VM, copy src/demo/greet.py and tests/test_greet.py back to the worktree and commit.

# Context
- The worktree HEAD already has greet(name, shout=False) and farewell(name). A leftover uncommitted "times" option from issue #3 was stashed and must not be popped or treated as this ticket. The main checkout /workspace/aibots/project-repo may be behind the worktree; the worker MUST use the bot-001 worktree. Do not implement only locally. Do not print API keys or tokens.

# Acceptance Criteria
- [ ] greet("Ada") returns "Hello, Ada!"
- [ ] greet("Ada", formal=True) returns "Good day, Ada."
- [ ] greet("Ada", formal=True, shout=True) returns "GOOD DAY, ADA."
- [ ] existing shout and farewell tests still pass
- [ ] all tests pass on the Simular box remote VM
- [ ] finished files are committed in the bot-001 worktree

# Attachments
- (none)

# Execution Log
## Triage (superagent)
- Assigned to bot-001: highest score 25 (hits: api, backend, python, tests, unittest)
- Moved to to-do

## Session started (bot-001)
- Session 9df910fd-3aef-4090-8dc1-3f039f2fdcb6 on branch bots/bot-001 at 2026-11-14 00:25
- Attachments copied: none

## Turn 1 (bot-001)
- Prompt sent at 2026-11-14 00:25 (session 9df910fd-3aef-4090-8dc1-3f039f2fdcb6)
- Prompt: Show me the VM test results and the final committed files.

## Turn 1 result (bot-001)
- Status: done at 2026-11-14 00:26
- Bot reply: **Done.** All acceptance criteria met.  **VM test results (8 tests, all OK):** - `test_greet` — "Hello, Ada!" - `test_greet_shout` — "HELLO, ADA!" - `test_greet_shout_defaults_false` — "Hello, Ada!" - `test_farewell` — "Goodbye, Ada!" - `test_greet_formal` — "Good day, Ada." - `test_greet_formal_sho

# Final Review
All acceptance criteria met. Implementation and tests ran on the Simular box remote VM using OpenMausBot's `box` MCP tools (box_ensure, shell, write_file, read_file).

**VM test results (8 tests, all OK):**
- test_greet — "Hello, Ada!"
- test_greet_shout — "HELLO, ADA!"
- test_greet_shout_defaults_false — "Hello, Ada!"
- test_farewell — "Goodbye, Ada!"
- test_greet_formal — "Good day, Ada."
- test_greet_formal_shout — "GOOD DAY, ADA."
- test_greet_formal_defaults_false — "Hello, Ada!"
- test_greet_formal_shout_defaults_false — "Good day, Ada."

**Files committed on branch bots/bot-001 (commit 765cbee):**
- src/demo/greet.py — added formal: bool = False parameter; formal greeting uses "Good day, {name}." (period), and shout uppercases it
- tests/test_greet.py — added 4 new tests covering formal, formal+shout, and default behaviors

The existing shout and farewell behavior is unchanged. The leftover times-option stash was not popped.
