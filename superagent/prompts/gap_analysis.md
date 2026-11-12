# Gap analysis prompt

Given the ticket's acceptance criteria and the turn diff + test output:

For each criterion output one line:
`- [x] <criterion> — evidence: <file:line | test name>` or
`- [ ] <criterion> — gap: <what is missing>`

Then:
- `Gaps:` bullet list (empty if none)
- `Risk:` anything outside scope, unsafe, or untested
- `Action:` `approve` or `request changes`
- `Follow-up prompt:` (only when requesting changes) a short, concrete instruction
  for the bot that lists the gaps and the tests that must pass.
