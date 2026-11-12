# Review policy (superagent)

After every bot turn the superagent MUST:

1. Fetch the turn diff (`bot_get_last_turn_diff`). Reject turns that touch files
   outside the ticket's scope, `.git/`, secrets, or CI credentials.
2. Run validation (`bot_run_tests` with the bot's `default_test_cmd`, plus any
   ticket-specific checks). A red test run is always a gap.
3. Gap analysis: walk every acceptance criterion and mark it met / not met with
   evidence (file + line or test name). Use `prompts/gap_analysis.md`.
4. Record the review with `ticket_add_review_note` (turn number, gaps, action).
5. Decide:
   - gaps and iteration < `loop.max_iterations_per_ticket` -> follow-up prompt
     to the same bot session listing only the gaps;
   - no gaps -> `bot_commit_turn`, then move to `in-review` (human approval) or
     `done` per `loop.human_review_column`;
   - iteration limit reached -> leave in `in-review` with a note asking a human.
6. Never mark done with failing tests, uncommitted bot changes, or unchecked
   acceptance criteria.

Security: never paste secrets into tickets or prompts; the permission gate is
authoritative for commands, even ones the superagent requests.
