---
id: 2026-11-13_add-a-shout-option-to-greet
title: Add a shout option to greet
status: done
assignee_bot: bot-001
github_issue: 2
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhIU79k
priority: medium
labels: [python, backend]
created_at: '2026-11-13'
updated_at: '2026-11-13'
---

# Objective
In the demo project, add a shout option to greet() in src/demo/greet.py (paths are relative to the bot worktree). When shout is true, greet should return the normal greeting in uppercase, e.g. greet("Ada", shout=True) returns "HELLO, ADA!". Default shout to false so existing greet("Ada") still returns "Hello, Ada!". Keep the existing farewell() function. Add or update tests in tests/test_greet.py (unittest).

# Context
- Code is in src/demo/greet.py. Tests are in tests/test_greet.py (unittest). The assigned worker is bot-001 (MausBot).

# Acceptance Criteria
- [ ] greet("Ada") returns "Hello, Ada!"
- [ ] greet("Ada", shout=True) returns "HELLO, ADA!"
- [ ] existing farewell tests still pass
- [ ] all tests pass

# Attachments
- (none)

# Execution Log
## Triage (superagent)
- Assigned to bot-001: highest score 22 (hits: backend, python, tests, unittest)
- Moved to to-do

## Turn 1 Review (superagent)
- Reviewed at 2026-11-13 09:01
- Review: shout option implemented successfully.
- greet("Ada") returns "Hello, Ada!" (backward compatible)
- greet("Ada", shout=True) returns "HELLO, ADA!" (new feature)
- farewell() function unchanged and tests pass
- All 4 tests pass: test_farewell, test_greet, test_greet_shout, test_greet_shout_defaults_false
- Commit: 5692824 on branch bots/bot-001

# Final Review
- (filled when done)
