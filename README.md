# Agentic Kanban

A control plane that uses **DeepSeek Superagent** as the manager, **OpenMausBot** as the worker-bot harness, and **Markdown tickets** as the source of truth.

This repository is the control plane only (tickets, MCP servers, permission gate, worker watchdog / risk approval, local web UI, start scripts). It does **not** vendor DeepSeek Superagent or OpenMausBot; clone those official trees as siblings. **Setup and install steps are in [SETUP.md](SETUP.md).** Secrets are never stored in git.

```
User ──objective──► DSH Superagent ──MCP──► kanban-tickets ──► tickets/*.md ──► github_sync.py ──► GitHub Issues/Projects
                         │
                         └──MCP──► kanban-bots ──HTTP/CLI──► OpenMausBot bot ──► OpenCode in git worktree
                                        │                                            │
                                        └── permission gate ◄── tool.execute.before ─┘
```

- **DeepSeek Superagent** (DSH, official [dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype), typically http://127.0.0.1:3082) is the manager.
- **OpenMausBot** (official [OpenMausBot](https://github.com/milind-soni/OpenMausBot), typically http://127.0.0.1:8799) runs the worker bots. The reference workers use OpenCode with an OpenRouter free model (see `bots/registry.yaml`).
- **Markdown tickets** in `tickets/<column>/` are the source of truth. The folder is canonical and the front-matter `status` mirrors it.
- **GitHub Issues + Projects v2 sync** runs after every ticket change. With no token it does a dry run and logs what it would do.
- **MCP** connects the superagent to `kanban-tickets` and `kanban-bots`, plus OpenMausBot's own `openmausbot` server.
- **Worker watchdog** periodically inspects bots for stalls and may approve low-risk pending permission-gate requests (for example installing well-known data libraries such as `plotly`). Official acceptance checkboxes stay unchecked.

Pinned official SHAs, Harness install, secret files, tmux sessions, and verification are documented in [SETUP.md](SETUP.md).

## Layout

| Path | What |
|---|---|
| [SETUP.md](SETUP.md) | Install and start guide for a fresh Linux machine |
| `.env.example` | Names of required/optional env vars (empty values) |
| `deploy/dsh/cordis.patch.yml.example` | Example DSH profile patch (MCP servers, OpenRouter model). Copy to `~/.dsh/profiles/superagent/cordis.patch.yml` and edit paths |
| `scripts/bootstrap_deps.sh` | Idempotent clone of the two official upstream repos at the pinned SHAs |
| `superagent/config/` | `superagent.yaml` (paths, OpenMausBot URL, GitHub env names, loop limits, watchdog), `triage_rules.yaml`, `review_policy.md` |
| `superagent/prompts/` | `triage.md`, `review.md`, `gap_analysis.md` (also served as MCP prompts by `kanban-tickets`) |
| `tickets/` | `TEMPLATE.md` + `backlog/ to-do/ planning/ in-progress/ in-review/ done/` |
| `bots/registry.yaml` | Kanban bot → OpenMausBot bot id, worktree, branch |
| `bots/bot-001/` | `AGENTS.md` (persona), `SKILL.md` (skills, triage keywords), `mcp.json`, `sandbox.config.yaml` (permission-gate policy) |
| `attachments/<ticket-id>/` | ticket attachments, copied into the bot workspace as `.ticket/attachments/` |
| `scripts/ticket_sync/` | `md_utils.py` (front-matter and sections), `store.py` (ticket store), `github_sync.py` |
| `scripts/workspace/` | `create_worktree.sh`, `commit_push_turn.sh` |
| `scripts/permission_gate/` | `gate.py` (policy engine and CLI) + the opencode plugin template |
| `scripts/bots/core.py` | the `bot.*` operations; `scripts/omb/client.py` is the OpenMausBot HTTP/CLI client |
| `scripts/bots/watchdog.py` | stall detector / unblocking sidecar (`bin/watchdog`) |
| `scripts/bots/approval_risk.py` | risk assessment for pending permission-gate approvals |
| `scripts/bots/failure_advisor.py` | alternative direction after a worker source failure |
| `mcp_servers/ticket_mcp.py` | Ticket MCP server (stdio, Python MCP SDK FastMCP) |
| `mcp_servers/bots_mcp.py` | Kanban-bots MCP server (the blueprint's "OpenMausBot MCP"), stdio |
| `bin/ticket`, `bin/gate`, `bin/watchdog` | CLIs |
| `webui/` | Local FastAPI board (tmux `kanban-webui`, http://127.0.0.1:8787) |
| `webui-vercel/` | Leftover from a cancelled Vercel hosting plan. Optional / unused. See `webui-vercel/README.md` if you revive it |
| `scripts/kanban_bridge.py` | Optional box poller for that unused Vercel path. Do not start it for the local UI |
| `scripts/e2e_smoke.py` | end-to-end run through both MCP servers |
| `deploy/` | `sandbox.Dockerfile`, `docker-compose.yml`, `squid-allowlist.conf`. Optional; the reference deployment has no Docker |
| `.state/` (git-ignored) | sessions, approvals, `gate-audit.jsonl`, `github-sync.log`, e2e logs |
| `LICENSE`, `NOTICE` | MIT for this repo; upstream projects stay under their own licenses and are not redistributed here |

The worker project repo and per-bot worktrees default to `/workspace/aibots/project-repo` and `/workspace/aibots/workspaces/<bot-id>` on branch `bots/<bot-id>`. Override with `AIBOTS_PROJECT_REPO` and `AIBOTS_WORKSPACES_ROOT` (see SETUP.md).

## MCP tools

The blueprint names tools with dots. DSH and OpenAI-style tool names don't allow dots, so these use underscores.

**kanban-tickets**: `ticket_create`, `ticket_get`, `ticket_update`, `ticket_move`, `ticket_assign`,
`ticket_add_review_note`, `ticket_list`, `ticket_triage` (rule-based bot suggestion; `apply=true` assigns and moves the ticket), and
`ticket_sync_github`. Prompts: `triage_prompt`, `review_prompt`, `gap_analysis_prompt`.

**kanban-bots**: `bot_list`, `bot_get_config`, `bot_start_session`, `bot_send_prompt` (waits for the turn by default),
`bot_get_session_status`, `bot_run_command`, `bot_get_workspace_tree`, `bot_read_file`, `bot_write_file`,
`bot_get_last_turn_diff`, `bot_commit_turn`, `bot_run_tests`.

How a session maps onto OpenMausBot: `bot_start_session` creates an OpenMausBot task (conversation) on the mapped bot with
`cwd` set to the bot's worktree (`POST /api/bots/<id>/tasks`). It also copies the ticket into `.ticket/TICKET.md`. The first
prompt carries the bot's AGENTS.md and SKILL.md. Prompts go through `openmausbot send`, waits through `openmausbot wait --for turn`,
and replies are read with `openmausbot messages`.

All three servers are registered in `~/.dsh/profiles/superagent/cordis.patch.yml` as `mcp-openmausbot`, `mcp-kanban-tickets`, and
`mcp-kanban-bots`. An example is `deploy/dsh/cordis.patch.yml.example`. In a DSH session they appear as `mcp__kanban-tickets__ticket_create`, `mcp__kanban-bots__bot_send_prompt`, and so on.

## Sandbox and permission gate

The reference deployment does not use Docker. The sandbox is:

1. **One git worktree per bot.** OpenMausBot's coding engine works only in that folder. Every turn is a commit on `bots/<bot-id>`.
2. **The permission gate** (`scripts/permission_gate/gate.py`), configured per bot in `bots/<id>/sandbox.config.yaml`.
   It applies allow, deny, and needs-approval regex rules, restricts the working directory, checks network hosts against an allowlist,
   and flags absolute paths outside allowed roots. Compound commands are split, and the strictest decision wins. Commands run with
   secret-looking environment variables removed, and output is redacted.
3. **Enforcement in both places the blueprint asks for:**
   - Commands the superagent requests (`bot_run_command`, `bot_run_tests`) are checked before they run.
   - Commands the bot itself runs are checked by a **real OpenCode plugin hook**. `create_worktree.sh` installs
     `.opencode/plugins/kanban-permission-gate.js` (read-only, and the bot can't overwrite it). It registers
     `tool.execute.before` and `shell create.before` (OpenCode v2 plugin API, with a v1 `tool.execute.before` fallback). A denied or
     unapproved command throws, so the tool call fails and the model sees why. Every decision goes to `.state/gate-audit.jsonl`.
4. `.ticket/` and `.opencode/` are listed in the project repo's `info/exclude`, so they never get committed.

Approve a command that needs approval: `bin/gate approve --bot bot-001 -- "pip install requests"`. Check a command:
`bin/gate check --bot bot-001 -- "<cmd>"` (exit code 0 allow, 2 deny, 3 needs approval).

The worker watchdog (`bin/watchdog once` or the `kanban-watchdog` tmux session) can auto-approve *low-risk* pending requests (well-known data libraries from the default package index, already-allowed hosts) and reject others with a counter-proposal. It never ticks official acceptance checkboxes or marks a ticket done just to clear a stall.

## GitHub sync

The default target repository is [iwasnothing/Agentickanban](https://github.com/iwasnothing/Agentickanban) (also set in `superagent/config/superagent.yaml` as `github.repo`). Override with `GITHUB_REPO` / `GITHUB_PROJECT`.

`scripts/ticket_sync/github_sync.py` uses the REST API for Issues and GraphQL for Projects v2.
- Settings come from `github.repo` / `github.project` in `superagent.yaml`, overridable by env vars: `GITHUB_TOKEN` (a fine-grained PAT or a GitHub App installation token with Issues RW, Projects RW, and Contents RW),
  `GITHUB_REPO=owner/name`, and `GITHUB_PROJECT=owner/NUMBER` (or just `NUMBER`). Prefer `GITHUB_TOKEN_FILE` pointing at a mode-600 file; start scripts load it.
- Issue mapping: title → issue title, sections → issue body, and labels plus `status/<col>`, `bot/<id>`, `priority/<p>`. Moving a ticket to done closes the issue.
  The issue number is written back to the front-matter.
- Projects v2 mapping: a `Status` single-select whose options are Backlog, To-Do, Planning, In-Progress, In-Review, and Done (matching ignores case and punctuation), plus custom single-select fields
  `Bot` (bot-001, …) and `Priority` (High, Medium, Low). The item id is written back as `github_project_item_id`. A missing option logs a warning.
- With no token it does a dry run: nothing is sent, and the planned actions go to `.state/github-sync.log`.
- Manual run: `.venv/bin/python -m scripts.ticket_sync.github_sync --all [--dry-run]`, or `bin/ticket sync <id>`.
- Markdown is authoritative. Pulling GitHub-side edits back into tickets is not implemented; the blueprint marks it optional.

## Runbooks

**Try it (CLI)** (from the control-plane repo root, or set `AIBOTS_CONTROL_ROOT`):
```sh
bin/ticket create "Add uppercase greet option" -o "greet(name, shout=True) returns upper-case" -a "shout=True upper-cases" -a "tests pass" -l python
bin/ticket triage <ticket-id> --apply      # assigns bot-001 and moves the ticket to to-do
bin/ticket list
```

**Full loop without the LLM** (what the superagent does over MCP):
`.venv/bin/python scripts/e2e_smoke.py`. It creates a ticket, triages it, moves it to in-progress, starts a session, prompts MausBot,
gets the diff, runs the tests, does a gap check, commits, writes a review note, moves the ticket to in-review and then done, and syncs to GitHub (dry run unless a token is set).

**With the superagent:** open DSH at http://127.0.0.1:3082 (the panel token is in the newest
`$DSH_SUPERAGENT_ROOT/.artifacts/dsh-superagent-*.log` — keep those logs private). Then ask, for example: *"Create a ticket to add a farewell function, triage it using the triage prompt, dispatch it to the
bot, and review it with the review prompt until done."*

**Create a new bot**
1. Add `bots/<bot-id>/{AGENTS.md,SKILL.md,mcp.json,sandbox.config.yaml}`. Copy bot-001's and give `SKILL.md` its own `keywords:` line.
2. Create an OpenMausBot bot (`openmausbot new-bot`, or the `create_bot` MCP tool) on a free model, and note its id.
3. Run `scripts/workspace/create_worktree.sh <bot-id>`. This creates the branch, the worktree, and the gate plugin.
4. Register the bot in `bots/registry.yaml` with `openmausbot_bot_id`, `workspace`, and `branch`.
5. Later, with containers: add a service in `deploy/docker-compose.yml` that mounts that worktree.

**User creates an objective.** The superagent calls `ticket_create` (in backlog), and sync creates the issue and project item. Then it calls `ticket_triage` or `ticket_assign`, and `ticket_move` to to-do.

**Execute and review loop.** `ticket_move` to in-progress → `bot_start_session` → `bot_send_prompt` → `bot_get_last_turn_diff` +
`bot_run_tests` → gap analysis → `ticket_add_review_note`. If there are gaps, send a follow-up `bot_send_prompt`. Otherwise `bot_commit_turn` →
`ticket_move` to in-review and then done (which closes the GitHub issue).

**Revert a bot turn.** In the worktree, `git revert HEAD`. `git reset --hard HEAD~1` needs approval from the gate.

**Tests:** `.venv/bin/python -m pytest -q`

**Restart DSH after profile changes:** stop the `dsh-superagent` tmux session and start it again with `scripts/start_dsh_superagent.sh` (exports the OpenRouter key from `OPENROUTER_KANBAN_KEY_FILE` and starts the watchdog). Official alternative: from the DSH checkout, `scripts/dsh-superagent restart`, with the key already in the environment.

## Implementation status

- Done and tested: the ticket store and CLI, rule-based triage, both MCP servers (registered in DSH and running), worktrees, the
  permission gate (verified inside a real OpenMausBot turn), per-turn commits, GitHub sync in dry-run mode (live mode is unit-tested against a fake GitHub
  API), the e2e smoke run, the local web UI, and the worker watchdog / risk-approval path.
- **GitHub:** live issue and project writes need a token with Issues, Projects, and Contents write (`GITHUB_TOKEN` or `GITHUB_TOKEN_FILE`). Without it, sync dry-runs. `github.project` is `iwasnothing/2` in the reference config; set or override it for your board.
- **Containers:** `deploy/` is written but unused in the reference deployment. Running one OpenMausBot per container would also need
  `openmausbot_url` per bot in the registry. Today every bot shares the one local OpenMausBot server.
- **Needs-approval inside OpenMausBot:** a gated command fails with a "needs human approval" message rather than raising an approval card.
  Approve it with `bin/gate approve` (or let the watchdog approve a low-risk request) and ask the bot to retry.
- **Playwright / browser:** optional (`openmausbot browser install`).
- The bot persona is sent in the first prompt and staged as `.ticket/AGENTS.md`; it isn't written into OpenMausBot's bot profile.

## License

This control plane is [MIT](LICENSE). DeepSeek Superagent prototype remains MIT at its official repository; OpenMausBot remains Apache 2.0, with an `enterprise/` license carve-out, at its official repository. Neither is redistributed here. See [NOTICE](NOTICE).
