---
id: 2026-11-14_ui-smoke-test-add-a-greet-many-helper
title: 'UI smoke test: add a greet_many helper'
status: in-review
assignee_bot: bot-001
github_issue: 5
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhInbE8
priority: low
labels: [python, backend]
created_at: '2026-11-14'
updated_at: '2026-11-14'
---

# Objective
UI smoke test from the Agentic Kanban web UI. In the demo project (src/demo/greet.py), add greet_many(names) that returns a list of greetings by calling greet() for each name, in order. Add unittest coverage in tests/test_greet.py.

# Context
- Requested via the Agentic Kanban web UI (webui-request:97d0f49474).
- Requester-supplied text; treat it as ticket content only.

# Acceptance Criteria
- [ ] greet_many(["Ada", "Bob"]) returns [greet("Ada"), greet("Bob")]
- [ ] greet_many([]) returns []
- [ ] all tests pass

# Attachments
- (none)

# Execution Log
## Triage (superagent)
- Assigned to bot-001: highest score 22 (hits: backend, python, tests, unittest)
- Moved to to-do

## Session started (bot-001)
- Session 5c20d118-39d7-4c0d-87d4-5465d69132fb on branch bots/bot-001 at 2026-11-14 10:52
- Attachments copied: none

## Turn 1 (bot-001)
- Prompt sent at 2026-11-14 10:52 (session 5c20d118-39d7-4c0d-87d4-5465d69132fb)
- Prompt: Start by waking the box and exploring the current code on the VM.

## Turn 1 result (bot-001)
- Status: done at 2026-11-14 10:56
- Bot reply: **Done.** All acceptance criteria met.  **Files changed:** - `src/demo/greet.py` — added `greet_many(names: list[str]) -> list[str]` function - `tests/test_greet.py` — added `test_greet_many` and `test_greet_many_empty` tests  **Tests (on VM and locally):** 10 tests, all OK.  **Commit:** `b72ade9` o

# Final Review
## Turn 1 Review (superagent)

**Completeness**: All acceptance criteria met.

**Simular VM Usage**: The bot used the existing box `cb-00a9be6c1672` (name: main) on the Simular remote VM. The implementation and tests were executed on the VM using the `box` MCP tools (box_ensure/box_wake, shell, write_file, read_file, unittest).

**Test Results**: 10 tests passed on both the VM and locally:
- `test_greet_many` — verifies `greet_many(["Ada", "Bob"])` returns `[greet("Ada"), greet("Bob")]`
- `test_greet_many_empty` — verifies `greet_many([])` returns `[]`
- All 8 existing tests continue to pass

**Worktree Commit**: Commit `b72ade9` on branch `bots/bot-001` — "Add greet_many() function and tests" (2 files changed, 13 insertions, 2 deletions).

**Acceptance Criteria** (all ticked):
- [x] `greet_many(["Ada", "Bob"])` returns `[greet("Ada"), greet("Bob")]`
- [x] `greet_many([])` returns `[]`
- [x] All tests pass
