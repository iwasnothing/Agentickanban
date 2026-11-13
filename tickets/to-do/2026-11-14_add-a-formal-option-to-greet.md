---
id: 2026-11-14_add-a-formal-option-to-greet
title: Add a formal option to greet
status: to-do
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

# Final Review
- (filled when done)
