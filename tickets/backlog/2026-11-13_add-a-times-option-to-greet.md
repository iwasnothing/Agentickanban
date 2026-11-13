---
id: 2026-11-13_add-a-times-option-to-greet
title: Add a times option to greet
status: backlog
assignee_bot: null
github_issue: 3
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhIWsus
priority: medium
labels: [python, backend]
created_at: '2026-11-13'
updated_at: '2026-11-13'
---

# Objective
In the demo project worktree, add a times option to greet() in src/demo/greet.py. greet("Ada", times=3) must return "Hello, Ada! Hello, Ada! Hello, Ada!" (the normal greeting repeated, separated by spaces). Default times to 1 so existing greet("Ada") still returns "Hello, Ada!". times must also work with shout: greet("Ada", shout=True, times=2) returns "HELLO, ADA! HELLO, ADA!". Keep farewell() unchanged. Add or update tests in tests/test_greet.py (unittest). The assigned OpenMausBot worker must implement and run the tests on the Simular box remote VM using its box MCP tools, then copy the finished files back into the bot-001 worktree and commit.

# Context
- Code is in the bot-001 worktree at /workspace/aibots/workspaces/bot-001 (branch bots/bot-001). That tree already has greet(name, shout=False) and farewell(name). The worker is bot-001 (MausBot on the local OpenMausBot server). Execution must happen on the remote Simular box VM: OpenMausBot already has an enabled MCP server named box (https://mcp.box.simular.ai/mcp). Use box_ensure or box_wake, then implement and run unittest on that VM (shell, write_file, or equivalent box tools). After tests pass on the VM, copy src/demo/greet.py and tests/test_greet.py back to the worktree and commit. Do not skip the remote VM and only edit the local worktree.

# Acceptance Criteria
- [ ] greet("Ada") returns "Hello, Ada!"
- [ ] greet("Ada", times=3) returns "Hello, Ada! Hello, Ada! Hello, Ada!"
- [ ] greet("Ada", shout=True, times=2) returns "HELLO, ADA! HELLO, ADA!"
- [ ] existing farewell tests still pass
- [ ] all tests pass on the Simular box remote VM
- [ ] finished files are committed in the bot-001 worktree

# Attachments
- (none)

# Execution Log

# Final Review
- (filled when done)
