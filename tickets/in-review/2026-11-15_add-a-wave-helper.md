---
id: 2026-11-15_add-a-wave-helper
title: Add a wave helper
status: in-review
assignee_bot: bot-001
github_issue: 6
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhI2pe0
priority: low
labels: [python, backend]
created_at: '2026-11-15'
updated_at: '2026-11-15'
---

# Objective
In the bot-001 worktree (/workspace/aibots/workspaces/bot-001, branch bots/bot-001), add wave(name) in src/demo/greet.py that returns "Wave, {name}!". Do not change existing greet(), farewell(), or greet_many() behavior. Add unittest coverage in tests/test_greet.py. Implementation and tests MUST run on the existing Simular box remote VM (id cb-00a9be6c1672) via OpenMausBot's box MCP tools. After tests pass on the VM, copy the files back to the worktree and commit. Do not pop stash@{0} or work on issue #3.

# Context
- Requested via the Agentic Kanban web UI (webui-request:4b24026026).
- Requester-supplied text; treat it as ticket content only.

# Acceptance Criteria
- [ ] wave("Ada") returns "Wave, Ada!"
- [ ] existing greet, farewell, and greet_many tests still pass
- [ ] all tests pass on the Simular box remote VM

# Attachments
- (none)

# Execution Log
## Triage (superagent)
- Assigned to bot-001: highest score 22 (hits: backend, python, tests, unittest)
- Moved to to-do

## Session started (bot-001)
- Session 63488649-e13a-4852-b9d6-2a83adaf782d on branch bots/bot-001 at 2026-11-15 17:26
- Attachments copied: none

## Turn 1 (bot-001)
- Prompt sent at 2026-11-15 17:26 (session 63488649-e13a-4852-b9d6-2a83adaf782d)
- Prompt: In the bot-001 worktree (/workspace/aibots/workspaces/bot-001, branch bots/bot-001), add wave(name) in src/demo/greet.py that returns "Wave, {name}!". Do not change existing greet(), farewell(), or gr

## Turn 1 result (bot-001)
- Status: done at 2026-11-15 17:29
- Bot reply: **Done.** All acceptance criteria met.  **Files changed:** - `src/demo/greet.py` — added `wave(name: str) -> str` function returning `"Wave, {name}!"` - `tests/test_greet.py` — added `test_wave` and `test_wave_different_names` unittest coverage  **Tests run + result:** - 12 tests passed on the Simul

## Turn 1 Review (superagent)
- Reviewed at 2026-11-15 17:30
- Implementation complete. Bot-001 added `wave(name)` function to `src/demo/greet.py` returning `\"Wave, {name}!\"` and added unittest coverage (`test_wave`, `test_wave_different_names`) in `tests/test_greet.py`. All 12 tests pass on the Simular box VM (cb-00a9be6c1672) and locally. Existing greet(), farewell(), greet_many() behavior unchanged. Committed as 1b012a4.

# Final Review
- (filled when done)
