# Review prompt

Review turn {turn} of bot `{bot_id}` on ticket `{ticket_id}` (session `{session_id}`).

1. `bot_get_last_turn_diff` – read the full diff.
2. `bot_run_tests` – run the bot's default test command (and any the ticket lists).
3. Apply `config/review_policy.md`.
4. Run the gap analysis prompt against the acceptance criteria.
5. `ticket_add_review_note` with: summary of change, test result, gaps, action
   (request changes | approve).
6. If approve: `bot_commit_turn` (message `bot({bot_id}): turn {turn} for ticket {ticket_id}`),
   then `ticket_move` to in-review or done.
   If request changes: `bot_send_prompt` with only the gaps, then review again.

Never tell the worker that a command, host, or package install "has been
approved" unless the permission gate actually approved it. A watchdog note,
reject, or deny is not an approval. Do not invent an approval to clear a stall.

