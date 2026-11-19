# Agentic Kanban Implementation Spec (as-built, for DGX Spark)

**Document type:** implementation-grade specification (not a brochure)  
**Source of truth:** the live tree and running processes on the development box as inspected on **2026-11-15** (Asia/Hong_Kong).  
**Goal:** a medium-size coding LLM should be able to reimplement the same architecture, file tree, APIs, data formats, MCP tool contracts, configs, and runtime behavior on an NVIDIA DGX Spark from this document alone.

## Document status

This describes the system **as it actually exists**, including uncommitted working-tree changes that are part of the running system. It does not restate earlier design blueprints except to note that they exist and must not be treated as the as-built path.

- Control repo: `/workspace/ai-bot-system` (git remote conceptually tracks https://github.com/iwasnothing/Agentickanban).
- On 2026-11-15 many as-built pieces were **present on disk but not committed**, including `webui/`, `scripts/start_kanban_webui.sh`, `scripts/start_kanban_tunnel.sh`, `scripts/start_kanban_mcp.sh`, `scripts/start_dsh_superagent.sh` edits, the `bot_send_prompt` wait-timeout raise (600s → 1800/3600s with sliced waits and activity logging), and live e2e scripts. A reimplementation must follow the **working tree described here**, not `HEAD` alone.
- Earlier Notion design pages exist (leave them untouched): “Kanban bots system”, “AI-Bot Management System Blueprint (OpenShell + Pi + DeepSeek SuperAgent on GKE)”, and Multi-Agent Kanban Platform Blueprint v2/v3/v4. Those are designs, not this as-built spec.
- **Do not git commit or force-push from this document’s publishing process.**
- **Secrets rule:** this document names environment variables and secret-file *paths* only. It never includes API keys, tokens, invite codes, session secrets, bridge secrets, or key-file contents.

## Secrets and secret-file paths (names only)

Never store secret values in git, tickets, prompts, or this spec. On the development box, secret files live under `/workspace/.secrets/` with mode `600`. Relevant names:

| Name | Role | Typical source on this box |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenAI-compatible cloud LLM key (also exported by the DSH start script from the same file as the Kanban-specific key) | start script reads `/workspace/.secrets/openrouter-user.key` (`OPENROUTER_KANBAN_KEY_FILE`) and exports both `OPENROUTER_KANBAN_API_KEY` and `OPENROUTER_API_KEY` |
| `OPENROUTER_KANBAN_API_KEY` | Env var name referenced by the DSH profile as `apiKeyEnv` | same key file as above |
| `GITHUB_TOKEN` | GitHub Issues + Projects sync | `/workspace/.secrets/github-token` (`GITHUB_TOKEN_FILE`) |
| `KANBAN_INVITE_CODE` | Web UI invite-code login | env, or `/workspace/.secrets/kanban-webui.env` (`KANBAN_WEBUI_ENV_FILE`), fallback file `/workspace/.secrets/kanban-invite-code` (generated if missing) |
| `SIMULAR_BOX_TOKEN` | Bearer token for Simular Box MCP (`https://mcp.box.simular.ai/mcp`) | env or `kanban-webui.env` |
| Session signing key | `itsdangerous` `URLSafeTimedSerializer` secret for the `ak_session` cookie | `/workspace/.secrets/kanban-webui-session.key` (generated if missing). There is no `SESSION_SECRET` env var in the local FastAPI UI. |
| `BRIDGE_SECRET` | Only for the cancelled Vercel poller (`scripts/kanban_bridge.py`) | leftover; not part of the production local-UI path |
| `BOX_TOKEN` | Not used by this system | Distinguish from `SIMULAR_BOX_TOKEN`. OpenMausBot may have its own Boat/BOX_TOKEN concepts; they are not the Simular Box MCP token. |

The web UI and permission gate redact secret-shaped strings in logs (GitHub PATs, OpenRouter `sk-or-…` keys, env assignments, the invite code).

---

# 1. Purpose, components, processes, ports, and data flow

## 1.1 Purpose

Agentic Kanban is a local, invite-gated board plus a manager/worker loop:

- A human (or a script) creates an objective on the board.
- **DSH Superagent** (DeepSeek Harness profile `superagent`) acts as the **manager**: it creates and triages Markdown tickets, assigns a Kanban bot, sends implementation prompts, reviews diffs/tests, writes review notes, moves columns, and syncs to GitHub.
- **OpenMausBot** runs the **worker** (Kanban `bot-001` ↔ OpenMausBot bot **MausBot**). Its coding engine is **OpenCode** (`modelSelection.instanceId` = `opencodeGo`).
- **Markdown files under `tickets/<column>/` are the source of truth.** GitHub Issues + Projects v2 are a sync target. The folder is canonical; front-matter `status` mirrors it.
- Work happens in a **git worktree** of a separate project repo, optionally with tools on a remote **Simular Box** VM exposed to OpenMausBot as an MCP server named `box`.

This is **not** the generic DSH Superagent GitHub/Feishu “comment `@agent` and get a PR” product loop, although the same `dsh-superagent` prototype process hosts that product. The Kanban manager loop is **prompt- and MCP-driven** (web UI prompt + `superagent/prompts/*` + `review_policy.md`), using the `kanban-tickets` and `kanban-bots` MCP servers.

## 1.2 Component diagram

```
                    HTTPS (optional free tunnel, ~2h)
 Browser ──invite──► FastAPI web UI  127.0.0.1:8787
                        │  /api/tasks (background job)
                        │  session/create + session/prompt
                        ▼
              DSH Superagent  127.0.0.1:3082  (tmux dsh-superagent)
                        │  MCP stdio
          ┌─────────────┼──────────────────────────┐
          ▼             ▼                          ▼
   kanban-tickets  kanban-bots              openmausbot mcp
   ticket_mcp.py   bots_mcp.py              `openmausbot mcp`
          │             │                          │
          ▼             ▼                          ▼
   tickets/*.md    BotManager ──HTTP/CLI──► OpenMausBot  127.0.0.1:8799
   github_sync.py  worktree + gate                    │
          │             │                    OpenCode (opencodeGo)
          ▼             ▼                    MCP server `box`
   GitHub Issues   /workspace/aibots/        https://mcp.box.simular.ai/mcp
   + Project v2    workspaces/<bot-id>       (SIMULAR_BOX_TOKEN)
                   branch bots/<bot-id>
```

ASCII equivalent of the control-repo README:

```
User ──objective──► DSH Superagent ──MCP──► kanban-tickets ──► tickets/*.md ──► github_sync.py ──► GitHub Issues/Projects
                         │
                         └──MCP──► kanban-bots ──HTTP/CLI──► OpenMausBot bot ──► OpenCode in git worktree
                                        │                                            │
                                        └── permission gate ◄── tool.execute.before ─┘
```

If Superagent is down, the web UI **fallback** creates the ticket in the Markdown store and runs rule-based `ticket_triage(apply=True)`. It does not start a bot session.

## 1.3 Processes, ports, and tmux (as observed 2026-11-15)

| Process | How it is supervised | Bind address | Port | Notes |
|---|---|---|---|---|
| Kanban web UI (`uvicorn app:app`) | tmux session `kanban-webui`, start script `scripts/start_kanban_webui.sh` | `127.0.0.1` | `8787` (`KANBAN_WEBUI_PORT`) | FastAPI + static HTML/JS |
| Public tunnel (`localtolink`) | tmux session `kanban-tunnel`, `scripts/start_kanban_tunnel.sh` | outbound wss/443 to `*.localtolink.com` | local port 8787 | Free links last ~2 hours; URL changes on reconnect; watchdog probes `/healthz` |
| DSH Superagent | tmux session `dsh-superagent`, start script `scripts/start_dsh_superagent.sh` (wraps `dsh --profile superagent --no-open`) | `127.0.0.1` | `3082` | HTTP 401 on `/` means the UI is up and auth is required |
| OpenMausBot server | **Not** in a control-repo tmux session. Observed as a long-running `node …/openmausbot/dist-server/index.js` listening on 8799. Data dir `~/.openmausbot`. | `127.0.0.1` | `8799` (`OMB_PORT`) | CLI `openmausbot` (v0.1.154). A stale `server.info` also mentioned webhook `127.0.0.1:8800`; the Kanban control repo does not use that webhook. |
| MCP stdio children | Spawned by DSH via `scripts/start_kanban_mcp.sh` and `openmausbot mcp` | stdio | n/a | Not long-lived listeners |

Health probes treat Superagent `/` as up on HTTP 200/204/**401**/403. OpenMausBot health uses `GET /api/health` (200 only) plus a process check for `openmausbot serve` or `openmausbot/dist-server`.

## 1.4 End-to-end data flow (UI → Done)

1. User signs in with the invite code; the UI sets a signed HttpOnly cookie `ak_session`.
2. User clicks **+** (“New task for Superagent”), fills title/objective/AC/priority/labels, optionally checks “Also let the Superagent run the bot loop”.
3. `POST /api/tasks` enforces job policy (one in-flight job; max 4/hour, override `KANBAN_MAX_TASKS_PER_HOUR`), then starts a background thread.
4. The job authenticates to DSH (token parsed from the newest `/workspace/dsh-superagent-prototype/.artifacts/dsh-superagent-*.log` line `token=…`, then a cookie from `GET /?token=`), calls RPC:
   - `session/create` with `cwd=/workspace/ai-bot-system`
   - `session/selectModel` with `provider=openrouter` and model `nvidia/nemotron-3-ultra-550b-a55b:free` (override `KANBAN_DSH_MODEL`)
   - `session/prompt` with a tool-use prompt (see §8). `clientTimeZone` is hardcoded `Asia/Tokyo` in this call (see Gaps).
5. Superagent is instructed to call, once, `mcp__kanban-tickets__ticket_create` (backlog), then `ticket_triage` with `apply=true`, then `ticket_sync_github`. If `run_loop` is true, it then moves to in-progress, starts a bot session, sends a prompt, reviews, notes, moves to in-review and possibly done, and syncs again.
6. `ticket_*` mutations write Markdown under `tickets/`, optionally auto-commit `tickets/` + `attachments/` in the control repo, and run `github_sync.py` (live if `GITHUB_TOKEN` + repo are set; otherwise dry-run).
7. `bot_start_session` ensures the worktree, copies the ticket into `<workspace>/.ticket/TICKET.md`, and `POST /api/bots/<omb-id>/tasks` with `cwd` = the worktree.
8. `bot_send_prompt` sends via `openmausbot send`, waits in slices (see §6), logs tool activity from `~/.openmausbot/events/<task_id>.ndjson`.
9. The worker (OpenCode) edits the worktree. Commands pass the permission gate (OpenCode plugin + `bot_run_command` / `bot_run_tests`). Work may use Simular `box` MCP tools. The manager is supposed to commit with `bot_commit_turn` (the bot persona says not to commit/push itself).
10. Review notes go to the ticket Execution Log as `## Turn N Review (superagent)`. Official Acceptance Criteria checkboxes are **human-in-the-loop only** (see §3.5). Moving a ticket to `done` is **not** acceptance; it closes the GitHub issue.

Job wait deadlines in the web UI thread: **40 minutes** if `run_loop`, else **12 minutes**. If DSH cannot be reached, or Superagent finishes without creating a ticket (detected via a `webui-request:<job_id>` marker in ticket text), the fallback store+triage path runs.

---

# 2. Control-repo directory tree

Root: `/workspace/ai-bot-system`. Python 3.13 venv at `.venv`. Tests: `pytest` (`pytest.ini` sets `testpaths = tests`). Runtime state is gitignored under `.state/`.

```
ai-bot-system/
  README.md                          Operator README (some sections lag the working tree)
  requirements.txt                   mcp>=1.10,<2 ; pyyaml>=6 ; pytest>=8
  pytest.ini
  .gitignore                         .venv/ .state/ __pycache__/ .secrets/ *.key .env .env.* (!.env.example)
  bin/ticket                         Wrapper → scripts/ticket_cli.py
  bin/gate                           Wrapper → python -m scripts.permission_gate.gate
  attachments/<ticket-id>/           Copied file attachments
  bots/registry.yaml                 Kanban bot → OpenMausBot id, worktree, branch, model
  bots/bot-001/
    AGENTS.md                        Persona and constraints (sent as first-prompt preamble)
    SKILL.md                         Skills + `keywords:` line used by rule-based triage
    mcp.json                         Project MCP servers this bot “provides” (empty on this box)
    sandbox.config.yaml              Permission-gate policy + default_test_cmd
  mcp_servers/
    __init__.py
    ticket_mcp.py                    FastMCP server name `kanban-tickets` (stdio)
    bots_mcp.py                      FastMCP server name `kanban-bots` (stdio)
  scripts/
    config.py                        Settings loader (superagent.yaml + env overrides)
    ticket_cli.py                    CLI used by bin/ticket
    triage.py                        Rule-based triage
    e2e_smoke.py                     Offline-ish MCP loop (no live Superagent required)
    live_kanban_*.py                 Live e2e drivers (working tree; not all committed)
    kanban_bridge.py                 Leftover Vercel box poller (not the production path)
    start_kanban_webui.sh
    start_kanban_tunnel.sh
    start_dsh_superagent.sh
    start_kanban_mcp.sh              MCP stdio wrapper (re-injects GITHUB_TOKEN; fixes GIT_CONFIG_*)
    start_kanban_bridge.sh           Leftover
    bots/core.py                     BotManager (all bot_* operations)
    omb/client.py                    OpenMausBot HTTP + CLI client + events snapshot
    ticket_sync/md_utils.py          Front-matter and sections
    ticket_sync/store.py             TicketStore
    ticket_sync/github_sync.py       Issues + Projects v2
    permission_gate/gate.py          Policy engine + CLI
    permission_gate/opencode-plugin/kanban-permission-gate.js.tmpl
    workspace/create_worktree.sh
    workspace/commit_push_turn.sh
  superagent/config/superagent.yaml  Paths, OMB waits, GitHub env names, loop limits
  superagent/config/triage_rules.yaml
  superagent/config/review_policy.md
  superagent/prompts/{triage,review,gap_analysis}.md
  tickets/TEMPLATE.md
  tickets/{backlog,to-do,planning,in-progress,in-review,done}/
  tests/                             Unit tests (see §15 / test plan)
  webui/app.py                       FastAPI app
  webui/static/{index.html,login.html}
  webui/tunnel/                      localtolink (and unused tunnelmole) Node deps
  webui/.venv/                       Separate venv: fastapi 0.162.7, uvicorn 0.54.0, itsdangerous 2.2.0, mcp 1.30.0, zstandard 0.25.0, pyyaml 6.0.3
  webui-vercel/                      CANCELLED leftover (do not present as production)
  deploy/                            docker-compose + sandbox Dockerfile + squid allowlist; UNUSED (no Docker on the dev box)
  docs/                              This specification
  .state/                            gitignored: sessions.json, webui-jobs.json, github-sync.log, github-project-cache.json, gate-audit.jsonl, approvals.json, tickets.lock, kanban-tunnel-url.txt, e2e artifacts, webui.log
```

Related trees **outside** the control repo:

| Path | Role |
|---|---|
| `/workspace/dsh-superagent-prototype` | DSH Superagent prototype (Node, linked into the DSH profile). Version `0.1.0`, engines Node `^22.19.0 \|\| >=24.0.0`, `dsh ^0.2.1-alpha.2`. |
| `~/.dsh/profiles/superagent/` | Live DSH profile (`cordis.patch.yml`, `package.json` with `dsh-superagent: link:/workspace/dsh-superagent-prototype`) |
| `~/.dsh/state/superagent` | DSH stateDir from the profile |
| `/home/box/.dsh/sessions/--workspace-ai-bot-system--/<sessionId>/session.v4.jsonl.zstd` | Session event log the web UI tails (`zstandard`) |
| `~/.openmausbot/` | OpenMausBot data dir (`config.json`, `bots.json`, `events/<task_id>.ndjson`, DBs) |
| `/workspace/aibots/project-repo` | Demo project git repo (`src/demo/greet.py`, `tests/test_greet.py`) |
| `/workspace/aibots/workspaces/<bot-id>` | Per-bot worktree on branch `bots/<bot-id>` |
| `/workspace/.secrets/` | Mode-600 secret files (paths only; never commit) |
| `/home/box/.local/bin/dsh` | DSH CLI 0.2.1-rc.3 |
| `/home/box/.local/bin/openmausbot` | OpenMausBot CLI 0.1.154 |

`deploy/` describes a later container layout (one OpenMausBot per bot, egress proxy). It is written but unused. Today every Kanban bot shares the one local OpenMausBot server.

---

# 3. Ticket Markdown schema

## 3.1 Columns (folders)

Canonical columns, in order (`scripts/config.py` `COLUMNS` and `superagent.yaml` `tickets.columns`):

`backlog`, `to-do`, `planning`, `in-progress`, `in-review`, `done`

The **folder is canonical**. On read and write, `status` in front-matter is forced to `path.parent.name`. Do not patch `status` via `ticket_update`; use `ticket_move`, which renames/moves the file.

Aliases accepted by `TicketStore._validate_column`: `todo` → `to-do`, `inprogress` → `in-progress`, `inreview`/`review` → `in-review`. Underscores and spaces become hyphens; case is lowered.

## 3.2 Ticket id and filename

- Default id: `{YYYY-MM-DD}_{slugified-title}` where `slugify` NFKD-ascii-folds, replaces non-alnum with `-`, lowercases, max 48 chars (fallback `ticket`).
- Collision: suffix `-2`, `-3`, …
- Validation: `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", ticket_id)`
- Path: `tickets/<column>/<ticket_id>.md`
- The first ticket for a given day+title wins the unsuffixed id.

## 3.3 Front-matter

Ordered keys (`md_utils.FRONT_MATTER_KEYS`):

```yaml
---
id: 2026-11-15_add-a-wave-helper
title: Add a wave helper
status: done
assignee_bot: bot-001          # or null
github_issue: 6                # int or null; written back by github_sync
github_project_item_id: PVTI_… # string or null; written back by github_sync
priority: low                  # high | medium | low
labels: [python, backend]
created_at: '2026-11-15'
updated_at: '2026-11-15'
---
```

`created_at` / `updated_at` are ISO dates (`YYYY-MM-DD`). `priority` must be one of `high`, `medium`, `low`.

## 3.4 Body sections (H1, fixed order)

`Objective`, `Context`, `Acceptance Criteria`, `Attachments`, `Execution Log`, `Final Review`

Unknown H1 sections are preserved after these. `md.build_body` / `set_section` / `append_section` rewrite the body from a section map.

Default empty/placeholder text on create:

- Context: `- (none)` if no context items
- Acceptance Criteria: `- [ ] (to be defined)` if none supplied
- Attachments: `- (none)` if none
- Execution Log: empty
- Final Review: `- (filled when done)`

Acceptance criteria supplied as a string list become `- [ ] <text>` checkboxes. If an item already starts with `[x]` / `[ ]`, that state is preserved.

## 3.5 Acceptance criteria are human-in-the-loop

**Hard rule for Superagent and OpenMausBot:** do **not** auto-tick the official Acceptance Criteria checkboxes in the ticket body. Moving a ticket to `done` is a workflow/column change (and closes the GitHub issue); it is **not** human acceptance.

Live tickets on this box as of 2026-11-15:

- Almost every later ticket in `done/` still has **all official AC boxes unchecked**, including the wave helper and the heavier research ticket, even though Superagent wrote a review and moved them to Done.
- The earliest ticket `2026-11-12_add-farewell-function` has 3/3 boxes checked. This document does not assert who ticked them; later operational practice is “leave official boxes for a human.”
- Gap-analysis output may contain pseudo-checkboxes (`- [x] criterion — evidence: …`) **inside a review note**, which is not the official AC section.
- `superagent/config/review_policy.md` still says “Never mark done with … unchecked acceptance criteria.” Live behavior after the first tickets does not follow that sentence. Reimplementers should implement the **human-in-the-loop checkbox rule** and treat the review-policy sentence as stale relative to production practice (see Gaps).

The board UI shows `ac_done/ac_total` counts parsed from official `- [ ]` / `- [x]` lines; unchecked-but-Done cards are expected.

## 3.6 Execution Log format

`TicketStore.add_log(ticket_id, heading, lines)` appends to `# Execution Log`:

```
## <heading>
- <line>
- <line>
```

Lines that already start with `- ` are not prefixed again. `updated_at` is refreshed. `sync=False` skips the GitHub/auto-commit hook (used for high-frequency prompt/progress lines).

Observed heading conventions:

| Heading | When |
|---|---|
| `Triage (superagent)` | Rule-based or LLM triage applied |
| `Session started (<bot_id>)` | `bot_start_session` |
| `Turn N (<bot_id>)` | Prompt sent (`count_turns` uses this pattern; excludes `(superagent)`) |
| `Turn N progress (<bot_id>)` | Sliced wait still running (every `progress_log_seconds`) |
| `Turn N result (<bot_id>)` | Wait finished or timed out |
| `Turn N commit (<bot_id>)` | `bot_commit_turn` succeeded |
| `Turn N Review (superagent)` | `ticket_add_review_note` (non-final). First line: `Reviewed at YYYY-MM-DD HH:MM` |

`md.count_turns(body, bot_id=None)` counts `## Turn (\d+) (...)` in the log, ignoring headings that say `(superagent)`.

Example (structure from a real short ticket; times are naive `datetime.now()` stamps from the process):

```
## Triage (superagent)
- Assigned to bot-001: highest score 22 (hits: backend, python, tests, unittest)
- Moved to to-do

## Session started (bot-001)
- Session <uuid> on branch bots/bot-001 at 2026-11-15 17:26
- Attachments copied: none

## Turn 1 (bot-001)
- Prompt sent at 2026-11-15 17:26 (session <uuid>)
- Prompt: <last line of the prompt, truncated to 200 chars>

## Turn 1 result (bot-001)
- Status: done at 2026-11-15 17:29
- Bot reply: <first 300 chars of the reply, newlines flattened to spaces>

## Turn 1 Review (superagent)
- Reviewed at 2026-11-15 17:30
- <review text>
```

If a wait times out while tools are still running, the result section must **not** say `Bot reply: (none)`. It should say `In-progress activity: …` and `No finished text reply yet; the turn was still running when the wait ended.`

## 3.7 Board display keys (`AK-N`)

The Markdown `id` is the filename stem (date-slug), **not** `AK-N`.

The web UI assigns a display `key`:

- If `github_issue` is set: `AK-<github_issue>` (e.g. issue 6 → `AK-6`).
- Else: `AK-L<n>` where `n` is the 1-based index of tickets without a GitHub issue, sorted by `(created_at, id)`.

`github_url` is `https://github.com/<repo>/issues/<n>` when an issue number exists. Default repo `iwasnothing/Agentickanban` (`GITHUB_REPO`).

## 3.8 Store API semantics (non-MCP)

Lock: exclusive `fcntl` lock on `.state/tickets.lock`.

Post-mutation hooks (`_after`): if `tickets.sync_github_after_mutation` (or env `AIBOTS_GITHUB_SYNC`) is true, call `sync_ticket` (failures are captured, local change is kept). If `tickets.auto_commit` (or `AIBOTS_AUTO_COMMIT`) is true, `git add -A tickets attachments` and commit `tickets: …` only if there is a staged diff.

`set_meta_quiet` writes GitHub ids back without triggering another sync.

`import_github_issue` exists for the cancelled Vercel path (bind to an existing issue, keep `from-ui`, drop `awaiting-triage`). Idempotent by `github_issue` number.

## 3.9 CLI (`bin/ticket`)

```
ticket create TITLE -o OBJECTIVE [-a AC]... [-c CONTEXT]... [--attach PATH]... [-p high|medium|low] [-l LABEL]... [--status backlog]
ticket move TICKET_ID TO_COLUMN
ticket assign TICKET_ID BOT_ID
ticket list [--status COL] [--bot BOT_ID]
ticket get TICKET_ID [--json]
ticket triage TICKET_ID [--apply]
ticket note TICKET_ID NOTE [--turn N]
ticket sync TICKET_ID
```

---

# 4. MCP tool contracts

Blueprint names used dots (`ticket.create`). DSH / OpenAI-style tool names do not allow dots, so implementations use underscores. Inside a DSH session they appear as `mcp__kanban-tickets__ticket_create`, `mcp__kanban-bots__bot_send_prompt`, `mcp__openmausbot__send_bot_message`, etc.

Both Kanban servers use the Python MCP SDK **FastMCP** (`mcp` 1.30.0) and run over **stdio**. Launch via:

```
/workspace/ai-bot-system/scripts/start_kanban_mcp.sh ticket_mcp.py
/workspace/ai-bot-system/scripts/start_kanban_mcp.sh bots_mcp.py
```

The wrapper sets `AIBOTS_CONTROL_ROOT`, default `GITHUB_REPO` / `GITHUB_PROJECT`, loads `GITHUB_TOKEN` from `GITHUB_TOKEN_FILE` if unset, and unsets a broken `GIT_CONFIG_COUNT` / `GIT_CONFIG_VALUE_0` pair when `GIT_CONFIG_KEY_0` is missing (see §7 and §14).

JSON schemas are inferred from Python type annotations (FastMCP). Return values are Python dicts/lists serialized as MCP tool results (typically JSON text in `content[0].text`, sometimes structured).

## 4.1 `kanban-tickets` (`mcp_servers/ticket_mcp.py`)

Server name: `kanban-tickets`.

Column type: `Literal["backlog", "to-do", "planning", "in-progress", "in-review", "done"]`  
Priority type: `Literal["high", "medium", "low"]`

### Tools

**`ticket_create`**

| Arg | Type | Default | Notes |
|---|---|---|---|
| `title` | str | required | |
| `objective` | str | required | |
| `acceptance_criteria` | list[str] \| None | None | |
| `context` | list[str] \| None | None | |
| `attachments` | list[str] \| None | None | Local file paths (copied to `attachments/<id>/`) or URLs kept as-is |
| `priority` | Priority | `"medium"` | |
| `labels` | list[str] \| None | None | |
| `status` | Column | `"backlog"` | |

Returns: `{ticket_id, path, github?, commit?}` (`github`/`commit` from `_after`).

**`ticket_import_github`**

| Arg | Type | Default |
|---|---|---|
| `github_issue` | int | required (existing issue; does not open a second issue) |
| `title` | str | required |
| `objective` | str | required |
| `acceptance_criteria` | list[str] \| None | None |
| `context` | list[str] \| None | None |
| `priority` | Priority | `"medium"` |
| `labels` | list[str] \| None | None |
| `status` | Column | `"backlog"` |
| `github_project_item_id` | str \| None | None |

Idempotent if a ticket already has that issue number. Returns `{ticket_id, path, imported, existing, ...}`. Written for the cancelled Vercel UI path.

**`ticket_get(ticket_id: str) -> dict`**

Returns `{ticket_id, path, metadata, sections, acceptance_criteria, markdown}` where `acceptance_criteria` is `[{done: bool, text: str}, ...]` and `sections` is `{heading: content}`.

**`ticket_update(ticket_id: str, patch: dict) -> dict`**

Allowed patch keys: `title`, `priority`, `labels`, `assignee_bot`, `github_issue`, `github_project_item_id`, `objective`, `context`, `acceptance_criteria` (list → checkboxes), `attachments`, `final_review`, `sections` (`{heading: markdown}`).  
**Forbidden:** `status`, `id`, `created_at` (status → `ticket_move`).  
Returns `{ticket_id, changed: [keys], ...}`.

**`ticket_move(ticket_id: str, to_column: Column) -> dict`**

Returns `{ticket_id, from, to, ...}`. Moving to `done` closes the GitHub issue on live sync (`state=closed`, `state_reason=completed`).

**`ticket_assign(ticket_id: str, bot_id: str) -> dict`**

`bot_id` must exist in `bots/registry.yaml` if the registry is non-empty. Returns `{ticket_id, assignee_bot, ...}`.

**`ticket_add_review_note(ticket_id: str, note: str, turn: int | None = None, final: bool = False) -> dict`**

If `final=true`, overwrites the Final Review section. Else appends `## Turn N Review (superagent)` to the Execution Log. `turn` defaults to `max(count_turns(body), 1)`.

**`ticket_list(status: Column | None = None, assignee_bot: str | None = None) -> list[dict]`**

Each row: `id`, `title`, `status`, `assignee_bot`, `priority`, `labels`, `github_issue`, `updated_at` (or `ticket_id`/`error` if a file is unreadable).

**`ticket_triage(ticket_id: str, apply: bool = False) -> dict`**

See §4.3. Returns scores plus `suggested_bot`, `suggested_column`, `reason`, `planning_triggers`, `applied`.

**`ticket_sync_github(ticket_id: str) -> dict`**

Force sync; dry-run when `GITHUB_TOKEN` is missing or repo is unset. Shape from `GitHubSync.sync`: `{ok, mode: "dry-run"|"live", issue, project_item, actions, warnings}`.

### Prompts (MCP prompts, not tools)

Loaded from `superagent/prompts/*.md` with `{placeholders}` replaced:

- `triage_prompt(ticket_id)`
- `review_prompt(ticket_id, bot_id, session_id, turn="1")`
- `gap_analysis_prompt()` (no args)

## 4.2 `kanban-bots` (`mcp_servers/bots_mcp.py`)

Server name: `kanban-bots`.

**`bot_list() -> list[dict]`**

Each item:

```
{
  bot_id, name, enabled,
  openmausbot_bot: {id, name, activity, busy, model},
  workspace, branch, workspace_ready,
  open_tickets: [ticket ids in to-do|planning|in-progress|in-review assigned to this bot]
}
```

If OpenMausBot is unreachable, `activity` is `"openmausbot-unreachable"`.

**`bot_get_config(bot_id: str) -> dict`**

`{bot_id, "AGENTS.md", "SKILL.md", mcp, sandbox, registry}` where `mcp` is parsed `mcp.json` or null, `sandbox` is parsed `sandbox.config.yaml`, `registry` is the registry entry with defaulted workspace/branch.

**`bot_start_session(bot_id: str, ticket_id: str) -> dict`**

Ensures worktree, stages `.ticket/TICKET.md` + attachments, creates an OpenMausBot task.  
Returns `{session_id, bot_id, ticket_id, workspace, openmausbot_task_cwd, attachments}`.  
`session_id` is the OpenMausBot `task.threadId`. Persisted in `.state/sessions.json`.

**`bot_send_prompt(bot_id: str, session_id: str, prompt: str, wait: bool = True, timeout_seconds: int | None = None) -> dict`**

**Important (as of 2026-11-15 working tree):** `timeout_seconds` default is **`None`**, which means “use `openmausbot.default_wait_seconds` from yaml” (1800). This replaced the previous annotation default of `600`. See §6 for wait behavior and return shape.

**`bot_get_session_status(bot_id: str, session_id: str) -> dict`**

Session bookkeeping fields merged with `last_reply` (`reply`, `digest`).

**`bot_run_command(bot_id: str, cmd: str, session_id: str | None = None) -> dict`**

Runs `bash -c cmd` in the worktree through the permission gate, with secret-like env vars stripped, output redacted, timeout from `command_timeout_seconds` (default 300).  
Allowed: `{status: "ok"|"failed", exit_code, stdout, stderr, decision}`.  
Blocked: `{status: "blocked"|"needs_approval", decision, hint?}`. Timeout: `{status: "timeout", exit_code: None, decision}`.  
If `session_id` is provided it must exist for that bot (validation only).

**`bot_get_workspace_tree(bot_id: str) -> dict`**

`{bot_id, workspace, branch, head, files, truncated, status, ticket_files}`. `files` is tracked ∪ untracked, capped at 500 (`max_entries` exists on the Python method but is **not** exposed as an MCP argument).

**`bot_read_file(bot_id: str, path: str) -> dict`**

Path relative to the worktree (absolute paths allowed only if inside the workspace). Max 512_000 bytes; `{path, content, truncated}`. Denied paths raise `BotError`.

**`bot_write_file(bot_id: str, path: str, content: str) -> dict`**

Write inside the workspace. Denied: `.git/`, and writes to `.opencode/plugins`. Returns `{path, bytes}`.

**`bot_get_last_turn_diff(bot_id: str, session_id: str) -> dict`**

Working-tree diff from `turn_base` (including untracked, via a temp `GIT_INDEX_FILE`). After a clean commit matching `turn_base`, returns the last committed turn’s diff (`mode: "committed"`).  
`{session_id, turn, mode, base, files_changed, diff, truncated}` with diff redacted and capped at 60_000 chars. `.ticket/` and `.opencode/` should not appear (repo `info/exclude`).

**`bot_commit_turn(bot_id: str, session_id: str, message: str | None = None) -> dict`**

Runs `commit_push_turn.sh`. Default message: `bot(<bot-id>): turn <n> for ticket <ticket-id>`. Returns the script’s JSON: `{committed, head, message, pushed, push}` or `{committed: false, reason: "nothing to commit", head}`. Push is `git push <first-remote> HEAD:refs/heads/bots/<bot-id>` if a remote exists; **not** a force push.

**`bot_run_tests(bot_id: str, session_id: str | None = None, test_cmd: str | None = None) -> dict`**

Default command: `sandbox.config.yaml` `default_test_cmd`, else `python3 -m unittest discover -s tests -v`. Same gate as `bot_run_command`, plus `test_cmd` and `passed` (`status == "ok"`).

## 4.3 Rule-based triage (`scripts/triage.py` + `triage_rules.yaml`)

Scoring, per enabled registry bot:

- `score += keyword_hits * weights.skill_keyword_hit` (default 3). Keywords come from a `keywords: a, b, c` line in `bots/<id>/SKILL.md`, matched as whole words against title + objective + context + AC + labels (lowercased).
- `score += label_boosts * weights.label_match` (default 5). `label_boosts` maps a label (e.g. `python`) to a list of bot ids.
- If the ticket has labels `mcp/<name>` and the bot’s `mcp.json` does not list that name under `mcpServers` keys or `provides`, add `weights.mcp_requirement_missing` (default -100).
- `score -= open_tickets * weights.open_ticket_penalty` (default 1). Open = tickets in `to-do`, `planning`, `in-progress` assigned to that bot.

Sort by `(-score, open_tickets)`. Assign if best score ≥ `min_score_to_assign` (0) and no missing MCP; else `default_bot` (`bot-001`) if that bot is eligible.  
Column: `planning` if any `planning_triggers` word appears in the text (`epic`, `multi-step`, `break down`, `architecture`); else `after_assign_move_to` (`to-do`). If no bot, suggested column is `backlog`.  
`apply=true` calls assign, move, and logs `Triage (superagent)`.

## 4.4 OpenMausBot MCP (`openmausbot mcp`, serverName `openmausbot`)

This is the upstream OpenMausBot stdio server (v0.1.154), not `kanban-bots`. Tools discovered from the installed package (names + descriptions; argument details below are from that package’s inputSchema where inspected):

| Tool | Role (short) | Notable args |
|---|---|---|
| `get_system_health` | Is the server reachable | — |
| `list_bots` | List bots / status / tasks | none |
| `get_bot_messages` | Recent messages for one task | `bot_id` (required), `task_id?`, `limit` default 30 max 200 |
| `send_bot_message` | Send instruction to a bot task without changing the selected task | `bot_id`, `text` (required), `task_id?`, `send_id?` |
| `create_bot` | Create a bot | `name` required; optional title/description/section/model |
| `update_bot_profile` | Safe profile fields | (not fully expanded here) |
| `list_channels` / `get_channel_messages` / `send_channel_message` / `create_channel` / `update_channel` | Multi-agent channels | — |
| `create_task` | Fresh conversation for a bot or channel | — |
| `switch_task` / `rename_task` | Task selection | — |
| `search_messages` | Search transcripts | `query` required |
| `wait_for_conversation` | Wait until a bot/channel task finishes, stalls, fails, or needs input | `target_type`: `bot`\|`channel`, `target_id`, `timeout_seconds`, optional `task_id` |
| `set_bot_model` | Change model when idle | — |
| `edit_bot_message` | Rewind/edit a user message | — |
| `list_available_models` | Configured models | — |
| `interrupt_conversation` | Interrupt one turn | — |

**Kanban fallback:** the web UI’s Superagent prompt says: if `bot_start_session` fails with `missing config key GIT_CONFIG_KEY_0`, use `mcp__openmausbot__send_bot_message` to MausBot (`8a7348b7-596f-4a9a-8c13-fa57a262ceaf`) instead. A live heavy-task run did use `send_bot_message`, `get_bot_messages`, and `wait_for_conversation` alongside the kanban-bots tools.

This document does not claim a complete argument schema for every OpenMausBot tool beyond the rows above; reimplementers should treat `openmausbot mcp` as an upstream dependency and read its live schema (`list_tools`) on the target machine.

---
# 5. BotManager, sessions, worktrees, permission gate, commit-turn, tests

Implementation: `scripts/bots/core.py` (`BotManager`). Settings from `load_settings()`. OpenMausBot client default URL `http://127.0.0.1:8799`, CLI from yaml (`/home/box/.local/bin/openmausbot` on this box).

## 5.1 Registry

`bots/registry.yaml` (as-built):

```yaml
bots:
  bot-001:
    name: Backend Python bot
    openmausbot_bot_id: 8a7348b7-596f-4a9a-8c13-fa57a262ceaf   # MausBot
    openmausbot_bot_name: MausBot
    model: opencodeGo/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free
    workspace: /workspace/aibots/workspaces/bot-001
    branch: bots/bot-001
    config_dir: bots/bot-001
    enabled: true
```

Code defaults if omitted: `workspace` = `{workspaces_root}/{bot_id}`, `branch` = `bots/{bot_id}`.

OpenMausBot’s own `bots.json` records MausBot with:

```json
"modelSelection": {
  "instanceId": "opencodeGo",
  "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
}
```

That is the coding backend: **OpenCode** (`opencodeGo`) talking to an OpenAI-compatible OpenRouter model id. On DGX Spark, keep the same contracts and point the OpenCode/OpenRouter-compatible provider at a local endpoint (see §13).

## 5.2 Sessions (`.state/sessions.json`)

Keyed by `session_id` (= OpenMausBot task `threadId`). Fields:

```
session_id, bot_id, ticket_id, openmausbot_bot_id, workspace,
started_at (isoformat seconds), turn (int), turn_base (git SHA at turn start),
commits: [{turn, commit, base, message}], persona_sent (bool)
```

Atomic write via `.tmp` + replace. `start_session` initializes `turn` from `count_turns` already in the ticket log.

## 5.3 Worktrees

`scripts/workspace/create_worktree.sh <bot-id> [project-repo] [workspaces-root]`

- Requires bot id `^[A-Za-z0-9][A-Za-z0-9_-]*$`.
- Defaults: `AIBOTS_PROJECT_REPO` or `/workspace/aibots/project-repo`; `AIBOTS_WORKSPACES_ROOT` or `/workspace/aibots/workspaces`.
- If the worktree already exists, prints `worktree exists` and refreshes plugins/excludes.
- Else `git worktree add` existing branch `bots/<bot-id>`, or `-b` to create it from the project repo HEAD.
- Appends `.ticket/` and `.opencode/` to the shared `info/exclude` (so they never get committed).
- Installs `.opencode/plugins/kanban-permission-gate.js` from the template (mode `0444`, placeholders `__PYTHON__`, `__CONTROL__`, `__BOT__`, `__WORKSPACE__`).
- Copies `AGENTS.md`, `SKILL.md`, `mcp.json` into `.ticket/` if present.
- Also unsets a broken `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_*` set before calling git (same class of bug as §7).
- Prints the worktree path as the last line (BotManager uses that).

`BotManager.ensure_worktree` invokes this script with env `AIBOTS_PROJECT_REPO` and `AIBOTS_WORKSPACES_ROOT`.

## 5.4 Start session details

1. `store.get(ticket_id)`; `ensure_worktree`.
2. Write `ticket["markdown"]` to `<ws>/.ticket/TICKET.md`; copy `attachments/<ticket-id>/*` to `<ws>/.ticket/attachments/`.
3. `omb.create_task(openmausbot_bot_id, title="[<ticket_id>] <title>"[:120], cwd=worktree)` → HTTP `POST /api/bots/{id}/tasks` body `{"title", "cwd", "select": false}`. Returns `res["task"]`; `session_id = task["threadId"]`.
4. Log `Session started` with `sync=False`.

The first `send_prompt` prepends a persona preamble (AGENTS.md + SKILL.md + “full ticket is in `.ticket/TICKET.md`”) and sets `persona_sent=True`. Subsequent prompts do not.

Send uses CLI: `openmausbot send --bot <omb-id> --task <session_id> --message-file <tmp.md> [--send-id kb-<8-of-session>-t<turn>-<8-hex>] --json --quiet`. Env includes `OMB_PORT` derived from the URL.

## 5.5 Permission gate (`scripts/permission_gate/gate.py`)

Decisions: `allow` | `needs_approval` | `deny`. Rank: deny > needs_approval > allow > default. Compound commands are split on `&&`, `||`, `;`, `|`, and newlines (quote-aware, best effort); the strictest segment wins. Whole-command deny rules run first (e.g. `curl | sh`).

Human approvals: `.state/approvals.json`, key = `sha256(bot_id + NUL + stripped cmd)[:24]`. CLI:

```
bin/gate check --bot bot-001 [--cwd DIR] -- <command...>   # exit 0 allow, 2 deny, 3 needs_approval; JSON stdout
bin/gate check-path --bot bot-001 --path FILE [--write]
bin/gate approve --bot bot-001 -- "<cmd>"
```

Every decision can be audited to `.state/gate-audit.jsonl`.

`scrubbed_env()` drops env vars whose names match `(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)` and sets `PYTHONDONTWRITEBYTECODE=1`. `redact()` also substitutes values of those env vars and PAT/key regexes.

Path checks: cwd must be the workspace (or a descendant). Absolute / `~` / `..` tokens in command arguments outside `allowed_roots` use `paths.outside_roots` (default `needs_approval`). `check_path` denies anything outside the workspace, any `.git/` path, and writes under `.opencode/plugins`.

Network: URLs whose host is not in `allowed_hosts` (or a subdomain) use `network.unknown_host` (bot-001: `needs_approval`). Allowed hosts for bot-001: `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `localhost`, `127.0.0.1`.

bot-001 `sandbox.config.yaml` highlights (full file is in the repo; copy it):

- `default: allow` for unmatched commands
- `default_test_cmd: python3 -m unittest discover -s tests -v`
- `command_timeout_seconds: 300`
- Deny: recursive `rm -rf /` (and home), `curl|wget | sh`, manual `.git/` paths, `sudo`, `mkfs`/`fdisk`/`shutdown`/`reboot`, `dd if=`, fork bomb, **`git push --force` / `-f`**, `docker.sock`, bare `printenv`/`env`
- Needs approval: package installs (`pip`/`npm`/…), recursive `rm`, `git reset --hard` / `git clean -f` / `git push` (non-force still needs approval), `ssh`/`scp`/`nc`/`telnet`
- Allow examples: `python3`, `pytest`, `ls`/`cat`/…, `git status|diff|log|show|add|commit|branch|revert|stash`

The **OpenCode plugin** (v2 `ctx.tool.hook("execute.before")` + `ctx.shell.hook("create.before")`, v1 fallback `"tool.execute.before"`) spawn-syncs the Python gate with a 15s timeout. Deny / needs_approval **throws**, so the tool call fails and the model sees the reason. Needs-approval inside OpenMausBot does **not** raise an interactive approval card; a human runs `bin/gate approve` and asks the bot to retry.

Unit tests in `tests/test_gate.py` encode many of these decisions (including `git push --force` → deny, `pip install` → needs_approval, `cat /etc/shadow` → needs_approval, cwd `/` → deny).

## 5.6 Commit-turn

`scripts/workspace/commit_push_turn.sh <workspace> <bot-id> <ticket-id> <turn> [message]`

- Unsets broken `GIT_CONFIG_*` as in create_worktree.
- `git add -A`; if nothing staged, print `{"committed": false, "reason": "nothing to commit", "head": "..."}` and exit 0.
- Commit with `user.name`/`user.email` from `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` or `bot-id` / `bot-id@aibots.local`.
- If a remote exists, **non-force** `git push <remote> HEAD:refs/heads/bots/<bot-id>`.
- Prints one JSON line. BotManager records the commit and logs it (`sync=False`).

**Never force-push.** The gate denies `git push --force` / `-f`. Revert a turn in the worktree with `git revert HEAD`. `git reset --hard HEAD~1` needs approval.

## 5.7 Tests

`bot_run_tests` is just `_run` of the default or supplied command. Output last 20_000 chars, redacted. `passed` is strictly `status == "ok"` (exit code 0 after an allowed command).

The demo project’s tests are `unittest` under `tests/`. Loop config `require_tests_green: true` is guidance for the Superagent prompt/policy, not a hard lock in Python.

---

# 6. `bot_send_prompt` wait behavior (as of 2026-11-15)

This is **working-tree** behavior (uncommitted relative to git `HEAD` at inspection time). Older committed code used a single `wait(..., timeout_s or default_wait_seconds defaulting to 600)` and logged `Bot reply: (none)` when the reply was empty.

## 6.1 Config and defaults

From `superagent/config/superagent.yaml` `openmausbot:`:

| Key | As-built value | Meaning |
|---|---|---|
| `default_wait_seconds` | **1800** (30 min) | Base wait when MCP `timeout_seconds` is `None` |
| `max_wait_seconds` | **3600** (60 min) | Keep waiting past the default **while tools are still active** |
| `progress_log_seconds` | **60** | Slice length and progress-log interval |

Python fallbacks in `BotManager.send_prompt` match those numbers. `OpenMausBot.wait` default is now `timeout_s: int = 1800`. MCP `timeout_seconds` default is **`None` (use yaml)**.

Why the raise: originally the wait was **600 seconds (10 minutes)**. A heavier research ticket ran about **32.5 minutes** wall clock (see §11) and that class of turn outlived the 10-minute wait, producing a timed-out result and a blank-looking reply log. The sliced wait + activity logging were added at the same time.

## 6.2 Algorithm

1. Increment `turn`, snapshot `turn_base = HEAD`, optionally prepend persona, `openmausbot send`.
2. Log `## Turn N (<bot>)` with prompt-sent time and the last prompt line (≤200 chars), `sync=False`.
3. If `wait=false`, return `{session_id, turn, receipt, status: "dispatched"}`.
4. If `wait=true`:
   - `base_s = timeout_seconds or default_wait_seconds`
   - `max_s = max(base_s, max_wait_seconds)`
   - `slice_s = max(1, progress_log_seconds)`
   - Loop until `max_s` elapsed:
     - `openmausbot wait --for turn --timeout <slice>s --on-input exit` (CLI timeout = slice + 60s in the subprocess).
     - Wait exit mapping: `0=done`, `1=failed`, `3=needs-user`, `4=timed-out`, else `exit-<code>`.
     - Break on `done` / `failed` / `needs-user`.
     - If `wait()` returns earlier than the slice (tests or a CLI glitch), sleep the leftover rather than spinning logs.
     - Else append `## Turn N progress (<bot>)`: `Still working at <stamp> (<elapsed>s elapsed): <activity>.` or `… no new tool activity; <activity>.` when the activity fingerprint is unchanged.
     - After `elapsed >= base_s`, **stop if the snapshot is not `active`** (turn no longer running). If still active, continue up to `max_s`.
5. Load `last_reply` and an activity snapshot. If reply is empty but the snapshot has a reply, use it.
6. Log `## Turn N result (<bot>)`:
   - `Status: <status> at <stamp> (<elapsed>s elapsed)`
   - If a text reply exists: `Bot reply: <first 300 chars>`
   - Else if tools/activity/active/summary: `In-progress activity: …` and, on `timed-out`, `No finished text reply yet; …`
   - Else: `No tool activity or text reply recorded.`
   - **Do not** log `Bot reply: (none)`.

Return dict includes `session_id`, `turn`, `receipt`, `status`, optional `detail` (redacted stderr), optional `reply` / `digest` / `reply_error`, optional `activity`.

## 6.3 Activity snapshot from OpenMausBot events

Path: `{OPENMAUSBOT_HOME or ~/.openmausbot}/events/<task_id>.ndjson` (one JSON object per line).

Parsed event types:

| `type` | Effect |
|---|---|
| `turn.started` | `turn_started=True`, `turn_complete=False` |
| `turn.completed` | `turn_complete=True` |
| `item.started` and `itemType=="tool"` | increment count for `title` (trimmed, max 80 chars); set `latest` |
| `item.completed` and `itemType=="assistant_text"` | capture `text` as snapshot `reply` |

`active = turn_started and not turn_complete`.  
`format_activity` example: `15 tools so far (execute ×12, websearch ×3); latest: execute; turn still running`.

Covered by `tests/test_bots_core.py` (`test_send_prompt_logs_progress_instead_of_blank_reply`, `test_activity_snapshot_from_events`).

## 6.4 Interaction with DSH MCP timeouts (pitfall)

The DSH profile sets `toolCallTimeoutMs` for `mcp-kanban-bots` to **900000 (15 minutes)**. That is **shorter** than `default_wait_seconds` (30 min) and `max_wait_seconds` (60 min). If Superagent calls `bot_send_prompt` with `wait=true`, the DSH MCP client may time out the tool call at 15 minutes while the Python server is still waiting. Reimplementers should raise `toolCallTimeoutMs` to at least `max_wait_seconds` plus margin, or have Superagent use `wait=false` plus `bot_get_session_status` / OpenMausBot `wait_for_conversation`. This mismatch exists on the as-built box.

`mcp-kanban-tickets` timeout is 120000 ms (2 min). `mcp-openmausbot` is 180000 ms (3 min).

---

# 7. GitHub Issues + Project sync

Implementation: `scripts/ticket_sync/github_sync.py`. Markdown is authoritative. Pulling GitHub-side edits back into tickets is **not implemented**.

## 7.1 Targets and env

From yaml `github:` (as-built) and env overrides:

| Setting | Yaml key | Env override | As-built value |
|---|---|---|---|
| Token | `token_env: GITHUB_TOKEN` | `GITHUB_TOKEN` | must be set for live mode; else dry-run |
| Repo | `repo` + `repo_env` | `GITHUB_REPO` | `iwasnothing/Agentickanban` |
| Project | `project` + `project_env` | `GITHUB_PROJECT` | `iwasnothing/2` (user project “Agentic Kanban”) |
| Status field | `status_field` | — | `Status` |
| Bot field | `bot_field` | — | `Bot` |
| Priority field | `priority_field` | — | `Priority` |
| Also label `status/<col>` | `status_labels` | — | true |
| Also label `bot/<id>` | `bot_labels` | — | true |

Dry-run when `--dry-run` is passed, **or** when token or repo is missing. Dry-run logs planned actions to `.state/github-sync.log` (prefix `[dry-run]`) and returns them; nothing is sent.

Auth header: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, User-Agent `aibots-ticket-sync`. REST for Issues (`https://api.github.com`); GraphQL for Projects v2.

**Token scopes (operational):** a **classic PAT** needs **`repo` + `project`** (or the equivalent fine-grained permissions: Issues RW, Projects RW, Contents RW). Fine-grained tokens have **403’d on Projects** in real operation on this system; prefer a classic PAT with those scopes, or a GitHub App installation token that actually has project access. Yaml comments still mention fine-grained PATs; treat the 403 pitfall as authoritative.

CLI: `.venv/bin/python -m scripts.ticket_sync.github_sync --all [--dry-run]` or `--ticket <id>`, or `bin/ticket sync <id>`. The CLI sets `AIBOTS_GITHUB_SYNC=0` to avoid recursive store hooks.

## 7.2 Mapping

- Issue title ← ticket title; body ← `##` sections for the six body sections plus a footer stating Markdown is source of truth.
- Labels ← ticket labels ∪ `status/<column>` ∪ `bot/<assignee>` ∪ `priority/<p>`.
- Done → issue `state=closed` (`state_reason=completed` on update). Other columns → `open`.
- On first create, write `github_issue` back via `set_meta_quiet`.
- Projects v2: resolve `owner/number` (if `project` is only a number, owner is the repo owner). Query `organization` then `user` for `projectV2`. Cache `{key,id,fields}` in `.state/github-project-cache.json`.
- If no `github_project_item_id`, `addProjectV2ItemById` then store the item id.
- Set single-select fields by normalized name (`[^a-z0-9]` stripped, case-insensitive) so `in-progress` matches `In-Progress`. Missing field/option → warning, no crash.
- Project board option names in use: Backlog, To-Do, Planning, In-Progress, In-Review, Done; Bot values like `bot-001`; Priority High/Medium/Low.

Manual run after mutations is automatic when `sync_github_after_mutation` is true (default).

## 7.3 The `GIT_CONFIG_KEY_0` bug (real)

**Symptom:** git in an MCP-spawned process exits with `missing config key GIT_CONFIG_KEY_0`.

**Cause:** some MCP wrappers / stdio spawners drop environment variable names that look like secrets, including names containing `KEY`. They may **drop `GIT_CONFIG_KEY_*` but leave `GIT_CONFIG_COUNT` (and sometimes `GIT_CONFIG_VALUE_*`)**. Git then believes `GIT_CONFIG_COUNT` entries exist and fails when `GIT_CONFIG_KEY_0` is missing. The same class of scrubbing can drop `GITHUB_TOKEN`, which silently forces GitHub sync into dry-run if the MCP child never re-injects it.

**Workarounds in the as-built tree:**

1. `scripts/start_kanban_mcp.sh`, `start_dsh_superagent.sh`, `start_kanban_webui.sh`: if `GIT_CONFIG_COUNT` is set and `GIT_CONFIG_KEY_0` is empty, `unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0`.
2. `create_worktree.sh` and `commit_push_turn.sh`: if any `GIT_CONFIG_KEY_i` for `i < COUNT` is missing, unset `GIT_CONFIG_COUNT` and `GIT_CONFIG_KEY_0..9` / `GIT_CONFIG_VALUE_0..9`.
3. `start_kanban_mcp.sh` re-reads `GITHUB_TOKEN` from `GITHUB_TOKEN_FILE` so live sync still works after DSH scrubs the token from the parent env.
4. Superagent prompt fallback: if `bot_start_session` fails with this error, use `mcp__openmausbot__send_bot_message` to MausBot instead (and `wait_for_conversation` / `get_bot_messages` as needed). This was used in a real heavy-task run.

Reimplementations must include (1)–(3) at every git and MCP spawn site, not only in one script.

---

# 8. Web UI

Local FastAPI app: `webui/app.py`, static `webui/static/{login.html,index.html}`. Bound to `127.0.0.1:8787`. Docs/redoc/openapi are disabled. Security headers on all responses: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store`.

This local UI is the **production path**. `webui-vercel/` is leftover (see §16).

## 8.1 Auth

- Invite code from `KANBAN_INVITE_CODE`, else `/workspace/.secrets/kanban-invite-code` (created with `token_urlsafe(12)` and mode 600 if missing).
- `POST /api/login` body `{"code": "…"}`. Compare with `hmac.compare_digest`.
- Rate limit: **10 attempts per IP per 10 minutes** (window 600s). IP from `cf-connecting-ip` or `request.client.host`. Over limit → HTTP 429 `{"error": "too many attempts, try again later"}`. Bad code → 401 `{"error": "invalid invite code"}`.
- Success: JSON `{"ok": true}` plus cookie **`ak_session`**: `URLSafeTimedSerializer` (salt `ak-session`, secret from `/workspace/.secrets/kanban-webui-session.key`), payload `{"u": "invitee", "t": <unix>}`, **HttpOnly**, `SameSite=lax`, `path=/`, `max_age=7 days`, `secure` iff `X-Forwarded-Proto` or the request scheme is `https`.
- Middleware: unauthenticated `/api/*` → 401 `{"error": "unauthorized"}`. Other paths redirect to `/login` with a **non-empty** HTML body (a zero-length redirect stalled the localtolink tunnel).
- Public: `/login`, `/api/login`, `/favicon.ico`, `/healthz`, `/static/login*`.
- `POST /api/logout` deletes the cookie.

## 8.2 Board UX

- Jira-like board titled “AK board”, crumb “Projects / Agentic Kanban”.
- **Columns** = the six statuses (labels: Backlog, To do, Planning, In progress, In review, Done).
- **Rows / swimlanes** = each registry bot, plus **Unassigned** (`L.id === '__none'`) for tickets whose `assignee_bot` is missing or not in the registry. Lanes are collapsible.
- Cards: title, labels, type icon, display `key` (`AK-N` or `AK-Ln`), priority icon, AC `done/total`, assignee avatar (or `?` Unassigned). Click opens a drawer with objective, context, official AC checkboxes (read-only in the UI), execution log entries, final review, GitHub link, timestamps.
- Toolbar: All / High priority / Open only filters; search box; counts.
- **10s auto-refresh:** `setInterval(tick, 10000)` runs `loadBoard`, `loadHealth`, `loadJobs` together.
- **+** FAB “New task for Superagent”: modal with Summary (required), Description/objective (required), Acceptance criteria (one per line), Priority, Labels, checkbox **“Also let the Superagent run the bot loop (assigned bot implements on the Simular VM, then review). Slower; uses free models.”** (`run_loop`). Quota text `used/limit this hour`.
- An activity banner appears while a job is `queued` or `running`. Toasts when a watched job finishes.
- Health badges in the header for **Superagent**, **OpenMausBot**, and **Simular VM** (green / amber / red) with latency and a tooltip. Checking starts with “Checking health…”.

Client validation is mirrored on the server (see routes). Title 4–120 chars; objective 10–4000; ≤12 AC lines of 300 chars; ≤6 labels, sanitized `[^a-z0-9-]` stripped, max 24 chars.

## 8.3 Health checks (must not create VMs)

Background `health_loop` every **15s**. Box results are cached **60s**.

- **Superagent:** HTTP GET `http://127.0.0.1:3082/` (up on 200/204/401/403) + `tmux has-session -t dsh-superagent`. Green if both; amber if one; else red. Detail notes that **401 = auth wall, up**. Tooltip includes `KANBAN_DSH_MODEL`.
- **OpenMausBot:** GET `http://127.0.0.1:8799/api/health` must be 200, plus `pgrep -f "openmausbot serve"` or `openmausbot/dist-server`. Also lists bot names from `GET /api/bots`.
- **Simular VM:** MCP streamable HTTP to `https://mcp.box.simular.ai/mcp` with `Authorization: Bearer $SIMULAR_BOX_TOKEN`, `call_tool("box_list", {})` only. **Never create, start, or wake a box on a health poll.** Prefer box id `cb-00a9be6c1672` if present in the list, else the first box. State `running` → green; `hibernated` / `starting` / `waking` / `queued` / `saving` → amber (hibernated detail: “wakes on demand when a bot needs it”); else red. If `SIMULAR_BOX_TOKEN` is unset, the check fails (red).

## 8.4 Job policy

- Persist jobs in `.state/webui-jobs.json` (last 200 written; API returns last 20).
- **One in-flight job:** any job `queued` or `running` and younger than 1 hour → `POST /api/tasks` returns 429.
- **Max 4 jobs created per rolling hour** (`KANBAN_MAX_TASKS_PER_HOUR`, default 4) → 429.
- On UI process start, leftover `queued`/`running` jobs are marked `failed` / “Interrupted by a web UI restart”.
- Job record: `id` (10 hex), `title`, `status`, `stage`, `message`, `run_loop`, `created_at`, `updated_at`, `events[]`, `ticket_id`, later `session_id`.

`GET /api/jobs` also returns `limit_per_hour`, `used_this_hour`, `in_flight`.

## 8.5 Routes

| Method | Path | Auth | Behavior |
|---|---|---|---|
| GET | `/healthz` | public | `{"ok": true}` (tunnel watchdog) |
| GET | `/login` | public | `login.html` |
| POST | `/api/login` | public | see §8.1 |
| POST | `/api/logout` | session | clear cookie |
| GET | `/` | session | `index.html` |
| GET | `/api/board` | session | `{columns, bots, tickets, generated_at, repo, project_url}` where `project_url` is `https://github.com/users/iwasnothing/projects/2` |
| GET | `/api/tickets/{ticket_id}` | session | detail + parsed log; 404 if missing |
| GET | `/api/health` | session | `{services: [superagent, openmausbot, box], now}` (entries omitted until first probe) |
| GET | `/api/jobs` | session | recent jobs + quota |
| POST | `/api/tasks` | session | create Superagent job (or 400/422/429) |

## 8.6 How create-task talks to Superagent

`run_job`:

1. Marker `webui-request:<job_id>` is placed in the ticket context so the poller can find the created file.
2. Deadline 40 min (`run_loop`) or 12 min.
3. `_dsh_cookie()` / `_rpc` as in §1.4. RPC envelope: `{"type":"client-request","rpcId": <uuid>, "method": <method>, "payload": <payload>}` posted to `http://127.0.0.1:3082/api/<method>` with the DSH cookie. Success: `result.ok` and `result.value`.
4. Prompt (`_build_prompt`) tells Superagent it is the Kanban manager; JSON ticket fields are **untrusted user content**; call tools rather than only planning; never print secrets.
   - Always: `ticket_create` **once** with those fields (`status` backlog), keep context lines verbatim (they contain the marker), `ticket_triage` `apply=true`, `ticket_sync_github`.
   - If `run_loop`: then the normal loop for **that ticket only**; for bot-001, work/tests should run on the existing Simular box via OpenMausBot’s `box` MCP; do not implement code in Superagent itself; GIT_CONFIG fallback to `send_bot_message`.
   - If not `run_loop`: stop after triage and sync.
5. The thread polls DSH session events on disk every 5s, records new `mcp__*` tool names, detects the ticket by marker, and may **nudge once** if the session is not running, a turn has ended, the ticket is still missing, and events have been idle >10s.
6. Fallback (`_fallback_create`) if DSH connect/RPC fails or Superagent never created a ticket: `TicketStore.create` + `triage(..., apply=True)` with context explaining the fallback. Fallback does **not** run the bot loop.

---

# 9. DSH Superagent

## 9.1 What it is (and is not)

- **DSH** = DeepSeek Harness CLI `dsh` (observed 0.2.1-rc.3) at `/home/box/.local/bin/dsh`.
- **DSH Superagent** = the research prototype at `/workspace/dsh-superagent-prototype` (MIT, “not an official DeepSeek product”), packaged as a DSH bundle and run in profile `superagent`.
- **Do not** install the unrelated VS Code/Cursor “Superagent” security plugin (id 71287587). This system is a **local process**, not that plugin.

The prototype’s own product loop (GitHub issue comments, Feishu, its built-in ticket runner) is documented in `/workspace/dsh-superagent-prototype/docs/` and `SETUP.md`. The **Kanban** loop uses DSH as a generic tool-using agent with extra MCP servers. Do not assume the prototype’s GitHub watcher is what moves Kanban tickets.

## 9.2 Install and start (as-built)

Profile directory: `~/.dsh/profiles/superagent/`

`package.json` bundles, in order: `@deepseek-ai/dsh-base`, `@deepseek-ai/dsh-web-app`, `dsh-superagent` (linked to the prototype checkout).

Start (tmux):

```sh
tmux new-session -d -s dsh-superagent /workspace/ai-bot-system/scripts/start_dsh_superagent.sh
```

The start script:

- Reads `OPENROUTER_KANBAN_KEY_FILE` (default `/workspace/.secrets/openrouter-user.key`), exports `OPENROUTER_KANBAN_API_KEY` and `OPENROUTER_API_KEY` (value never on the command line).
- Sets `DSH_PERMISSION_MODE` default **`danger-full-access`** (unattended MCP/tool use; the DSH default policy is `ask`).
- Unsets a broken `GIT_CONFIG_COUNT` pair if needed.
- `exec /home/box/.local/bin/dsh --profile superagent --no-open`

The prototype also ships `scripts/dsh-superagent` (`start`/`stop`/`restart`/`status`) which runs `dsh --profile superagent` in tmux session `dsh-superagent` and writes logs under `/workspace/dsh-superagent-prototype/.artifacts/`. The Kanban start script is a thinner wrapper used on this box. After profile edits, restart the `dsh-superagent` tmux session (do not leave a second instance). HTTP listen: `127.0.0.1:3082`. The web UI finds the UI token by regex `token=(\\S+)` in the newest `.artifacts/dsh-superagent-*.log` (never log or copy that token into tickets or this spec).

## 9.3 Profile / MCP wiring (`~/.dsh/profiles/superagent/cordis.patch.yml`)

As inspected (secret values omitted; `apiKeyEnv` is an **environment variable name**):

- `webserver`: `host: 127.0.0.1`, `port: 3082`
- `dsh-superagent`: `stateDir: /home/box/.dsh/state/superagent`
- `llm-pi-ai` provider `openrouter`:
  - `apiKeyEnv: OPENROUTER_KANBAN_API_KEY`
  - `api: openai-completions`
  - `baseURL: https://openrouter.ai/api/v1`
  - model id: `nvidia/nemotron-3-ultra-550b-a55b:free`
- `agent-default-model`: `provider: openrouter`, same model id
- MCP clients (`@deepseek-ai/dsh-mcp-client`), all `transport: stdio`, `failOnStartupError: false`:

| serverName | command | args | env keys | toolCallTimeoutMs |
|---|---|---|---|---|
| `openmausbot` | `/home/box/.local/bin/openmausbot` | `mcp` | `OMB_PORT=8799` | 180000 |
| `kanban-tickets` | `…/scripts/start_kanban_mcp.sh` | `ticket_mcp.py` | `AIBOTS_CONTROL_ROOT`, `GITHUB_REPO`, `GITHUB_PROJECT` | 120000 |
| `kanban-bots` | `…/scripts/start_kanban_mcp.sh` | `bots_mcp.py` | same | 900000 |

The model provider is a **pluggable OpenAI-compatible endpoint**. On this box it is OpenRouter. On DGX Spark, change `baseURL` (and `apiKeyEnv` if needed) to a local server, and change the model id to whatever that server exposes. Keep `api: openai-completions` unless the local stack needs a different DSH api type (verify against DSH’s provider schema; do not invent one).

## 9.4 Manager loop (prompt-driven)

There is no separate Python “manager daemon” besides DSH itself and the web UI job thread. The loop is:

1. **Intake** — `ticket_create` (backlog) from the UI prompt or a human/DSH chat.
2. **Triage** — `ticket_triage` (rule-based suggestion) and/or the `triage_prompt`; `ticket_assign` + `ticket_move` to `to-do` or `planning`. Planning triggers can spawn child tickets (`ticket_create` referencing the parent in Context).
3. **Dispatch** — `ticket_move` to `in-progress`; `bot_start_session`; `bot_send_prompt` with the implementation instruction (first prompt includes persona).
4. **Review** — `bot_get_last_turn_diff`, `bot_run_tests`, `gap_analysis_prompt` against official AC, `ticket_add_review_note`.
5. **Iterate** — if gaps and iteration < `loop.max_iterations_per_ticket` (3), `bot_send_prompt` with only the gaps; else leave in `in-review` with a human note.
6. **Advance** — if no gaps: `bot_commit_turn`, `ticket_move` to `loop.human_review_column` (`in-review`) or `done` if that config is changed. Then `ticket_sync_github`.

`review_policy.md` also says to reject turns that touch out-of-scope files, `.git/`, or secrets, and that a red test run is always a gap. Official AC boxes must not be auto-ticked (§3.5).

Yaml `loop:` keys: `max_iterations_per_ticket: 3`, `require_tests_green: true`, `human_review_column: in-review`.

---
# 10. OpenMausBot and the remote Simular VM

## 10.1 Install / serve

- CLI: `openmausbot` (observed version **0.1.154**), realpath `/home/box/.local/lib/node_modules/openmausbot/cli.js`.
- Data directory: `~/.openmausbot` (`OPENMAUSBOT_HOME` overrides this in the Kanban client when reading events).
- Serve (typical): `openmausbot serve --port 8799` or `openmausbot start`. The yaml comment says `openmausbot serve --local`; current `--help` lists `--port`, `--data-dir`, `--lan`, `--tunnel`, `--tailscale`, `--domain`, `--relay`, but not a flag literally named `--local`. On a Spark, prefer `serve --port 8799` bound to localhost, or `--lan` on a trusted LAN — not a public tunnel unless you have a stable one.
- On this box the server was observed as `node …/dist-server/index.js` listening on `127.0.0.1:8799`. It is **not** supervised by a control-repo tmux session; there is no `start_openmausbot.sh` in the control repo. A `server.info` file under `/workspace/openmausbot-logs/` recorded `url=http://127.0.0.1:8799` and `data=/home/box/.openmausbot`. Reimplementers should add explicit supervision (tmux/systemd) on Spark.
- MCP stdio: `openmausbot mcp` (registered in the DSH profile with `OMB_PORT=8799`).
- Headless commands used by `scripts/omb/client.py` always add `--json --quiet`:
  - `send --bot --task --message-file [--send-id]`
  - `wait --bot --task --for turn --timeout <Ns> --on-input exit`
  - `messages --bot --task --limit`

HTTP used by the client:

- `GET /api/bots` → `{bots: [...]}` (health in the client is this endpoint; the web UI additionally uses `GET /api/health`)
- `GET /api/bots` filtered by id
- `POST /api/bots/{bot_id}/tasks` `{title, cwd, select: false}` → `{task: {threadId, cwd, ...}}`

Other OpenMausBot HTTP routes exist in the upstream product and are not inventoried here.

## 10.2 Config on this box (structure only)

`~/.openmausbot/config.json` contains:

- An `openrouter` key **value** (secret; do not copy). On Spark, replace this with a local provider configuration supported by OpenMausBot/OpenCode.
- `mcpServers`:
  - `dsh-superagent`: stdio `node /workspace/mcp-bridge/dsh-superagent-server.mjs`, **`enabled: false`**. Not part of the Kanban production path.
  - **`box`**: `type: http`, `url: https://mcp.box.simular.ai/mcp`, `headers.Authorization: Bearer <SIMULAR_BOX_TOKEN>`, **`enabled: true`**.

Distinguish carefully:

| Thing | What it is |
|---|---|
| MCP server name **`box`** | Simular Box MCP at `https://mcp.box.simular.ai/mcp` |
| **`SIMULAR_BOX_TOKEN`** | Bearer token for that MCP (Kanban web UI health + OpenMausBot `box` server) |
| OpenMausBot **Boat / `BOX_TOKEN`** | A different upstream concept. **Do not** reuse or confuse it with `SIMULAR_BOX_TOKEN`. On this box `BOX_TOKEN` was not part of the Kanban wiring. |

## 10.3 Bot mapping

| Kanban id | Display | OpenMausBot id | OpenMausBot name | Coding engine | Model (this box) |
|---|---|---|---|---|---|
| `bot-001` | Backend Python bot | `8a7348b7-596f-4a9a-8c13-fa57a262ceaf` | MausBot | OpenCode (`opencodeGo`) | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` |

Create additional bots with `openmausbot` / `create_bot`, then add `bots/<id>/{AGENTS.md,SKILL.md,mcp.json,sandbox.config.yaml}`, run `create_worktree.sh`, and register them in `registry.yaml`.

## 10.4 Simular Box VM

- Remote Linux VM used as a tool-using environment for some tickets (tests “must run on the existing Simular box”).
- Exposed to the worker through OpenMausBot MCP server **`box`**, not through `kanban-bots`.
- Health path calls **only** `box_list`. It does not create a box. It does not wake a hibernated box.
- The web UI currently prefers box id `cb-00a9be6c1672` (hardcoded). That id is a resource identifier on this account, not a secret, but **Spark should not hard-code it**. Make it configurable or omit the remote VM and run tests in the worktree.
- Tickets and the Superagent `run_loop` prompt tell the worker to use the **existing** box and, if it is hibernated, to wake it **when work needs it**, then copy results back to the worktree. The exact Simular MCP tool names for wake/exec (other than `box_list`) were **not fully inventoried** from the remote schema in this inspection (see Gaps). Do not invent them; list tools from the live `box` MCP on the machine that has a token.
- **Never create a new box from a health poll, a board refresh, or a monitoring loop.**

Playwright / browser: `openmausbot browser install` is documented as the way to add a browser; it is **not installed** on the development box. `SKILL.md` tells the bot to ask the manager if a browser is required.

---

# 11. End-to-end sequences and real timings

Timings below are **illustrations from real runs**, not SLAs. Timezone: e2e summaries that label `HKT` are Asia/Hong_Kong. Some ticket log stamps are naive `datetime.now()` from a process that may be using `+09` (Asia/Tokyo). The web UI’s DSH `session/prompt` hardcodes `clientTimeZone: "Asia/Tokyo"`. Treat stamps as approximate.

## 11.1 Intended happy path

1. UI or CLI creates a backlog ticket (or Superagent `ticket_create`).
2. Triage assign + move to `to-do` (or `planning`).
3. Move to `in-progress`; `bot_start_session`; `bot_send_prompt` (wait).
4. `bot_get_last_turn_diff` + `bot_run_tests` (and/or evidence that tests ran on the VM).
5. Gap analysis + `ticket_add_review_note`. Follow-up prompt if needed (max 3 iterations).
6. `bot_commit_turn`; move to `in-review`; optional human acceptance (tick official AC).
7. Move to `done` (closes GitHub issue). Official AC boxes may still be unchecked; that does **not** mean a human accepted.

Offline/scripted approximation: `scripts/e2e_smoke.py` walks both MCP servers with a fake or live OMB depending on the script, without requiring the DSH UI.

## 11.2 Observed short tasks (~4–6 minutes)

From `.state` e2e summaries (Asia/Hong_Kong where labeled):

| Ticket | GitHub / key | Duration | Outcome | Official AC boxes |
|---|---|---|---|---|
| Add a formal option to greet | issue 4 / AK-4 | **255 s** (~4.3 min) | `done` | unchecked |
| UI smoke: greet_many helper | issue 5 / AK-5 | **352 s** (~5.9 min) | `done` | unchecked |
| Add a wave helper | issue 6 / AK-6 | **16:25:21–16:31:00 HKT** (~5 min 39 s) | `done`; project status Done | unchecked (explicitly noted “human-in-the-loop”) |
| Add a times option to greet | issue 3 / AK-3 | **246 s** in one measured Superagent session | left in **`in-review`**; verification recorded `GIT_CONFIG_KEY_0` failures, 5 LLM retries, 4 rate-limit hits, 1 nudge | all six official boxes still unchecked |
| Add farewell function (earliest smoke) | issue 1 | `.state/e2e-last-run.json` `2026-11-12T23:14:17`–`23:14:25` (this particular smoke is not a full live LLM loop) | `done` | **checked** (exception; see §3.5) |

Short “add a helper to `greet.py`” tasks that completed successfully clustered around **six minutes**.

## 11.3 Heavier research task (~32.5 minutes)

Ticket id `2026-11-15_hong-kong-district-residential-price-per-sqft-ch` (GitHub issue 7, display key `AK-7`), created via the web UI with `run_loop=true`.

- E2E summary: `elapsed_s: **1948**` (~32 min 28 s), `finished_hkt: 2026-11-15 20:44:49 HKT`, column `done`.
- Official AC boxes: **0/6 checked** after Done (do not treat Done as acceptance). Do not copy any numeric business results from that ticket into a reimplementation or this spec.
- Superagent tools observed on that run included `ticket_create`, `ticket_triage`, `ticket_move`, `ticket_sync_github`, `ticket_add_review_note`, `bot_start_session`, `bot_send_prompt`, `bot_get_session_status`, `bot_get_workspace_tree`, `bot_read_file`, and the OpenMausBot fallbacks `send_bot_message`, `get_bot_messages`, `wait_for_conversation`.
- This is the class of turn that **outlived the original 10-minute (`default_wait_seconds: 600`) wait**, which is why the wait was raised to 1800/3600 with progress logging. Do not invent a more precise “hit at 10:00” timestamp; the code history and this duration are the evidence.

## 11.4 Web UI job timing bounds

The UI job thread’s own deadline is 40 minutes for `run_loop` and 12 minutes otherwise. That is independent of `bot_send_prompt`’s wait. A job can be marked done (or fall back) while a bot turn is still running.

---

# 12. Process supervision

## 12.1 tmux sessions (this box)

| Session | Start | Purpose |
|---|---|---|
| `kanban-webui` | `tmux new-session -d -s kanban-webui /workspace/ai-bot-system/scripts/start_kanban_webui.sh` | FastAPI UI |
| `kanban-tunnel` | `scripts/start_kanban_tunnel.sh` | public HTTPS via localtolink |
| `dsh-superagent` | `scripts/start_dsh_superagent.sh` | manager |

OpenMausBot is not in that list (see §10.1). The cancelled poller would have used `kanban-bridge`.

`start_kanban_webui.sh` also sources `/workspace/.secrets/kanban-webui.env` if readable (without overriding env already set), loads `GITHUB_TOKEN` from file, exports default repo/project, fixes `GIT_CONFIG_*`, then:

```
exec "$ROOT/webui/.venv/bin/uvicorn" app:app --host 127.0.0.1 --port "${KANBAN_WEBUI_PORT:-8787}" \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log
```

## 12.2 Public tunnel notes (free localtolink)

This development box only allows outbound TLS on 443. Cloudflare quick tunnels (port 7844), ssh tunnels, tunnelmole (8083), and similar were blocked, so the start script uses **localtolink** (`wss` on 443) from `webui/tunnel/node_modules/.bin/localtolink`.

Behavior encoded in `start_kanban_tunnel.sh`:

- `localtolink --port $PORT --json --no-qr`
- Parses `https://[a-z0-9-]+.localtolink.com` into `.state/kanban-tunnel-url.txt`
- Log: `.state/kanban-tunnel.log`
- **Watchdog:** every 30s, `curl` `<url>/healthz`. After **3 failures**, kill the client and reconnect. The URL **changes on every reconnect**.
- Free links last **about 2 hours** (or can be closed server-side).
- A **502** can appear at the public URL **while the local client process still looks connected**. The watchdog exists because process liveness is not enough.
- Unauthenticated redirects from the UI must have a non-empty body (tunnel stall).

**For DGX Spark, do not depend on this free tunnel.** Prefer:

- bind the UI to a LAN address behind a firewall, or
- a stable reverse proxy (Caddy/nginx/Tailscale) with HTTPS,

and treat localtolink as a last-resort demo hack.

---

# 13. Deployment on NVIDIA DGX Spark

Treat Spark as a **local all-in-one host**: UI + Superagent + OpenMausBot + local models + ticket store. Replace cloud LLM calls with a local OpenAI-compatible server.

## 13.1 Public hardware/software facts (do not invent beyond this)

From NVIDIA’s public DGX Spark pages and docs (https://www.nvidia.com/en-us/products/workstations/dgx-spark/ and the DGX Spark user / porting guides), as of this writing:

- NVIDIA **GB10 Grace Blackwell** Superchip: **20-core Arm CPU** (10× Cortex-X925 + 10× Cortex-A725) and an integrated **Blackwell** GPU.
- Public docs also cite **6,144 CUDA cores**, 5th-gen Tensor Cores, 4th-gen RT Cores.
- **Unified LPDDR5X memory**: **128 GB** in NVIDIA’s own user-guide table; the product page also notes a **64 GB** configuration **available exclusively through participating OEM partners**. Memory bandwidth **273 GB/s**, 256-bit interface. Unified memory architecture (CPU and iGPU share the same space; no fixed VRAM carve-out).
- Storage: NVMe M.2, public range **1 TB or 4 TB**, self-encrypting.
- Compact desktop; GB10 TDP **140 W**; system power supply listed at **240 W** on the product page.
- Networking: 10 GbE RJ-45, ConnectX-7, Wi-Fi 7, etc.
- Software: **NVIDIA DGX OS**; DGX OS 7 User Guide lists **DGX Spark** under ARM64, minimum ISO **7.2.3**, Ubuntu 24.04-based stack. Porting guidance mentions CUDA **13.0** and the NVIDIA AI software stack.
- NVIDIA’s marketing/user guide also states support for large local models (user guide: “up to 200 billion parameters (or 405B for dual-Spark configuration)”). Treat that as a vendor capacity claim, not a promise that any particular model will fit with this stack’s overhead.

If a fact is not on NVIDIA’s public pages, **do not invent it** (exact SKU of the user’s unit, measured tokens/s, whether a given 550B-class model fits in 128 GB, etc.). Measure on the machine.

## 13.2 Architecture mapping

| This box (cloud-assisted) | Spark (recommended) |
|---|---|
| DSH provider `openrouter` at `https://openrouter.ai/api/v1`, model `nvidia/nemotron-3-ultra-550b-a55b:free` | Same DSH `api: openai-completions` contract, `baseURL` → `http://127.0.0.1:<port>/v1` (vLLM, SGLang, Ollama, or OpenCode against a local model). Choose a model that actually runs on the installed memory. |
| OpenMausBot / OpenCode `openrouter/nvidia/…:free` | Point OpenCode’s OpenAI-compatible provider at the same local endpoint. Keep `instanceId: opencodeGo` if OpenCode remains the coding engine. |
| `OPENROUTER_*` keys | Local server may need no key, or a dummy key; put any real key in a 600-mode file, never in yaml. |
| Simular `box` VM | Optional. Prefer running tests in the worktree on Spark itself (plenty of local CPU/GPU). If you keep Simular, do not health-poll-create boxes; make the box id configurable. |
| Free localtolink on 443 | **LAN bind or a stable reverse proxy / Tailscale.** |
| Invite-code UI on 8787 | Keep loopback or LAN-only; keep invite code + HttpOnly cookie. |

A Hong Kong billing address on OpenRouter can block some model providers. Local models on Spark avoid that class of outage.

## 13.3 aarch64 / ARM64 and NVIDIA GPU packaging

- Install **aarch64** builds of Node (22.19+ or 24), Python 3.11+, git, tmux, pnpm (for DSH Superagent), and CUDA/GPU drivers from the DGX OS stack — not x86_64 wheels or Docker images.
- Prefer NVIDIA-provided containers or wheels for vLLM / SGLang / PyTorch on GB10. If a dependency has no aarch64 wheel, that is a porting task (see NVIDIA’s DGX Spark porting guide), not something to paper over with qemu.
- Unified memory: size the local model and the agent processes together. Leave headroom for OpenMausBot, DSH, Node, and the UI.
- DSH Superagent engines: Node `^22.19.0 || >=24.0.0`, `dsh ^0.2.1-alpha.2`. This box ran Node v24.22.1 and `dsh` 0.2.1-rc.3.

## 13.4 What to copy vs rewrite

**Copy (adapt paths only):**

- The entire control repo layout: `scripts/`, `mcp_servers/`, `superagent/config` + `prompts`, `bots/`, `tickets/` (empty columns + `TEMPLATE.md`), `webui/`, `bin/`, `tests/`, `requirements.txt`.
- Start scripts, GIT_CONFIG workarounds, permission-gate plugin template.
- Ticket Markdown schema and MCP tool names/argument shapes.

**Rewrite / re-point:**

- Absolute paths (`/workspace/...`, `/home/box/...`) in `superagent.yaml`, `registry.yaml`, `sandbox.config.yaml`, start scripts, and the DSH profile.
- LLM `baseURL`, model ids, `KANBAN_DSH_MODEL`, `DSH_PROVIDER` (today hardcoded `"openrouter"` in `webui/app.py` — make this configurable).
- `clientTimeZone` (today `Asia/Tokyo` in the UI’s DSH prompt call) and any naive timestamps; use the operator’s zone (this user is Asia/Hong_Kong).
- Hardcoded Simular box id and “must run on the Simular box” ticket boilerplate, unless that VM is still in use.
- GitHub repo/project if the Spark host uses a different GitHub target.
- Tunnel supervision: replace or drop `start_kanban_tunnel.sh`.
- Add a first-class OpenMausBot supervisor.

**Leave unused:** `webui-vercel/`, `scripts/kanban_bridge.py`, `deploy/` until you actually want containers.

## 13.5 Recommended implementation order (for a medium LLM)

1. Recreate the control repo skeleton, venv, `scripts/config.py`, ticket store + `md_utils` + CLI; run `tests/test_tickets.py`.
2. Implement `github_sync.py` with dry-run; run `tests/test_github_sync.py`; only then add a PAT with `repo` + `project`.
3. Permission gate + unit tests; worktree and commit scripts with the GIT_CONFIG workaround.
4. `BotManager` + OpenMausBot client with a **FakeOMB** (see `tests/test_bots_core.py`); implement the 1800/3600 sliced wait and activity logs **before** going live.
5. MCP servers; run `tests/test_mcp_servers.py`.
6. Local OpenAI-compatible model server; verify a trivial chat completion.
7. OpenMausBot + OpenCode pointed at that server; one manual `send` / `wait` in a worktree.
8. DSH profile with MCP wiring and local `baseURL`; `DSH_PERMISSION_MODE` chosen deliberately (this box uses `danger-full-access` for unattended loops — tighten if Spark is shared).
9. Web UI + start scripts; health checks that **list only**.
10. A small live e2e (create → triage → one bot turn → review → in-review) and the unit test suite.
11. Optional GitHub live sync and optional remote VM.

## 13.6 Test plan

**Unit tests already in the control repo** (`cd /workspace/ai-bot-system && .venv/bin/python -m pytest -q`):

- `tests/test_tickets.py` — front-matter roundtrip, lifecycle, aliases, triage hooks, no status patch.
- `tests/test_gate.py` — allow/deny/needs_approval matrix, cwd restriction, approvals.
- `tests/test_github_sync.py` — dry-run log; live path against a fake transport (issue create, GraphQL project fields).
- `tests/test_mcp_servers.py` — stdio `list_tools` (and a real `ticket_create` in a temp control root).
- `tests/test_bots_core.py` — full fake turn (persona, diff excludes `.ticket`/`.opencode`, gate, commit message, tree); progress-log test asserting **no** `Bot reply: (none)`; ndjson snapshot parser.
- `tests/conftest.py` builds an isolated control root + git project repo and unsets GitHub env.

**Small e2e on Spark (add this):**

1. Start OpenMausBot, local model server, DSH, and the UI (no public tunnel required).
2. `POST /api/login` + `POST /api/tasks` with `run_loop=true` for a trivial ticket (“add `ping()` returning `'pong'` and a unittest”).
3. Assert: ticket appears in `to-do` then `in-progress`; a session is created; a turn result is logged; tests pass in the worktree; a `bot(…): turn 1 for ticket …` commit exists; ticket ends in `in-review` or `done`.
4. Assert official AC checkboxes are **still unchecked** unless a human ticked them.
5. Kill Superagent and create another task: fallback must still create + triage a Markdown ticket.
6. Health endpoint: repeated `/api/health` must not create a Simular box (if `box` is configured, box count stays constant).
7. GitHub sync dry-run without a token; live sync only in a disposable project.

Live scripts `scripts/live_kanban_*.py` are environment-specific drivers, useful as references, not as portable pytest.

---

# 14. Known pitfalls from real operation

1. **`GIT_CONFIG_KEY_0`** — MCP spawners drop `GIT_CONFIG_KEY_*` but leave `GIT_CONFIG_COUNT`. Fix at every git/MCP entrypoint; fallback to `send_bot_message` if a session start still fails (§7.3).
2. **Long-turn blank replies** — a 10-minute wait on a ~32.5-minute research turn produced empty-looking logs (`Bot reply: (none)` in the old code). Use 1800/3600 sliced waits and log in-progress tool activity instead.
3. **Human AC boxes** — Superagent/OpenMausBot must not auto-tick official Acceptance Criteria. Done ≠ accepted. The earliest ticket’s checked boxes are not a pattern to copy. `review_policy.md` still contradicts live practice (§3.5).
4. **Health polls must not create boxes** — only `box_list`. Cache results. Never `box_create` from `/api/health`.
5. **Tunnel expiry / 502** — free localtolink lasts ~2 hours; 502 can happen while the client still looks up. Watchdog + URL file. Prefer LAN/reverse proxy on Spark.
6. **GitHub fine-grained token 403s on Projects** — use a classic PAT with `repo` + `project` (or an installation token that truly has Projects RW).
7. **Do not force-push** — denied by the gate; commit-push script never uses `--force`.
8. **OpenRouter billing geography** — a Hong Kong billing address can block some model providers. Local models on Spark avoid this.
9. **DSH `toolCallTimeoutMs` (15 min) < bot wait (30–60 min)** — Superagent may abandon a waiting `bot_send_prompt` early (§6.4).
10. **`GITHUB_TOKEN` scrubbed from MCP env** — without `start_kanban_mcp.sh` re-injection, sync silently becomes dry-run.
11. **Needs-approval is not an interactive card** inside OpenMausBot; approve with `bin/gate approve` and retry.
12. **Timezone split** — UI prompts `Asia/Tokyo`; host intended zone is Asia/Hong_Kong; many logs are naive local time.
13. **One in-flight UI job / 4 per hour** — easy to mistake for a hang.
14. **Uncommitted as-built tree** — copying only `git HEAD` misses the UI, start scripts, and wait-timeout raise.
15. **Hardcoded provider/model/box id** in `webui/app.py` — will be wrong on Spark until parameterized.
16. **Do not restart, stop, or reconfigure** the running demo services just to inspect them (operational rule on the development box).

---

# 15. Config reference (names and defaults, no secret values)

## 15.1 Environment variables

| Name | Default if unset | Purpose |
|---|---|---|
| `AIBOTS_CONTROL_ROOT` | directory containing `scripts/config.py`’s repo root | Control repo path |
| `AIBOTS_STATE_DIR` | yaml `paths.state_dir` or `<root>/.state` | Sessions, locks, logs |
| `AIBOTS_PROJECT_REPO` | yaml or `/workspace/aibots/project-repo` | Project git repo |
| `AIBOTS_WORKSPACES_ROOT` | yaml or `/workspace/aibots/workspaces` | Worktree parent |
| `AIBOTS_AUTO_COMMIT` | yaml `tickets.auto_commit` (true in yaml) | Commit ticket file changes |
| `AIBOTS_GITHUB_SYNC` | yaml `tickets.sync_github_after_mutation` (true) | Sync after mutations |
| `AIBOTS_PYTHON` | `<control>/.venv/bin/python` | Interpreter baked into the gate plugin |
| `GITHUB_TOKEN` | empty → dry-run | GitHub auth |
| `GITHUB_TOKEN_FILE` | `/workspace/.secrets/github-token` | File fallback |
| `GITHUB_REPO` | yaml `github.repo` | `owner/name` |
| `GITHUB_PROJECT` | yaml `github.project` | `owner/NUMBER` or `NUMBER` |
| `KANBAN_INVITE_CODE` | file fallback / generated | UI login |
| `KANBAN_WEBUI_ENV_FILE` | `/workspace/.secrets/kanban-webui.env` | Optional env file for UI |
| `KANBAN_WEBUI_PORT` | `8787` | UI bind port |
| `KANBAN_MAX_TASKS_PER_HOUR` | `4` | UI job quota |
| `KANBAN_DSH_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b:free` | Model id passed to `session/selectModel` (provider is hardcoded `openrouter` in app.py) |
| `DSH_PERMISSION_MODE` | `danger-full-access` in the start script | Unattended tool use |
| `OPENROUTER_KANBAN_API_KEY` | from key file in start script | DSH `apiKeyEnv` |
| `OPENROUTER_API_KEY` | also exported by the start script | Other tools that expect this name |
| `OPENROUTER_KANBAN_KEY_FILE` | `/workspace/.secrets/openrouter-user.key` | |
| `SIMULAR_BOX_TOKEN` | empty → Simular health red | Box MCP bearer |
| `OMB_PORT` | derived from OMB URL or `8799` | CLI/MCP → local server |
| `OPENMAUSBOT_HOME` | `~/.openmausbot` | Events dir root |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | `<bot-id>` / `<bot-id>@aibots.local` | Turn commits |
| `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_*` / `GIT_CONFIG_VALUE_*` | must be consistent or unset | See §7.3 |
| `BRIDGE_SECRET`, `KANBAN_VERCEL_URL`, `SESSION_SECRET` | leftover Vercel path | **Not** used by the local UI |

## 15.2 `superagent.yaml` keys (as-built)

```yaml
paths:
  control_repo: /workspace/ai-bot-system
  project_repo: /workspace/aibots/project-repo
  workspaces_root: /workspace/aibots/workspaces
  state_dir: /workspace/ai-bot-system/.state
tickets:
  columns: [backlog, to-do, planning, in-progress, in-review, done]
  canonical: folder
  auto_commit: true
  sync_github_after_mutation: true
openmausbot:
  url: http://127.0.0.1:8799
  cli: /home/box/.local/bin/openmausbot
  default_wait_seconds: 1800
  max_wait_seconds: 3600
  progress_log_seconds: 60
github:
  repo: iwasnothing/Agentickanban
  project: iwasnothing/2
  token_env: GITHUB_TOKEN
  repo_env: GITHUB_REPO
  project_env: GITHUB_PROJECT
  status_field: Status
  bot_field: Bot
  priority_field: Priority
  status_labels: true
  bot_labels: true
loop:
  max_iterations_per_ticket: 3
  require_tests_green: true
  human_review_column: in-review
```

## 15.3 Ports and timeouts

| Port | Bind | Service |
|---|---|---|
| 8787 | 127.0.0.1 | Kanban web UI |
| 3082 | 127.0.0.1 | DSH Superagent HTTP/UI |
| 8799 | 127.0.0.1 | OpenMausBot |

| Timeout | Value |
|---|---|
| bot wait default / max / slice | 1800 s / 3600 s / 60 s |
| `OpenMausBot.wait` subprocess | `timeout_s + 60` |
| Gate command timeout | 300 s (per bot yaml) |
| OMB HTTP client | 30 s |
| GitHub HTTP | 30 s |
| Web UI health HTTP | 4 s; box MCP 15–25 s; box cache 60 s; health loop 15 s |
| UI job deadline | 12 min (no loop) / 40 min (loop) |
| Login window | 10 tries / 10 min / IP |
| Session cookie | 7 days |
| DSH MCP toolCallTimeoutMs | tickets 120s, openmausbot 180s, bots 900s |
| OpenCode plugin gate spawn | 15 s |
| Tunnel watchdog | probe 30 s, restart after 3 failures; connect grace 15 s; retry 10 s |

## 15.4 Other constants

- Cookie name: `ak_session`
- UI serializer salt: `ak-session`
- Max file read: 512_000 bytes; command output cap 20_000; diff cap 60_000
- Bot-001 default test command: `python3 -m unittest discover -s tests -v`
- Demo project: `/workspace/aibots/project-repo` (`src/demo/greet.py`)
- Control-repo Python deps: `mcp>=1.10,<2` (installed 1.30.0), `pyyaml>=6` (6.0.3), `pytest>=8` (9.2.0)
- Web UI venv also has FastAPI 0.162.7, uvicorn 0.54.0, itsdangerous 2.2.0, zstandard 0.25.0 (for DSH session logs)

---

# 16. Out of scope / leftover Vercel split

`webui-vercel/` and `scripts/kanban_bridge.py` / `start_kanban_bridge.sh` were built as a public Vercel board plus a box poller (`tmux kanban-bridge`) that heartbeats health and imports GitHub issues labeled `awaiting-triage` / `from-ui`. That split was **cancelled because the user does not want to store secrets in Vercel**. Mention it only as leftover code. **Do not** treat it as the target architecture. The production path is the local FastAPI UI (`webui/`) plus optional LAN/reverse-proxy access.

`ticket_import_github` remains in the ticket MCP server for that leftover path and is still useful if you bind to an already-created issue without opening a duplicate.

`deploy/` (Docker sandbox + squid allowlist) is written but unused.

---

# 17. Gaps (not inferred as fact)

The following were unclear or inconsistent in the inspected tree. Do not invent APIs to fill them.

1. **Exact Simular Box MCP tool contract** besides `box_list` (wake, exec, file copy) was not listed from a live `list_tools` against `https://mcp.box.simular.ai/mcp` in this pass. Use the live schema; do not guess tool names.
2. **OpenMausBot process supervision** on this box has no control-repo start script; the exact long-term launch command for the current PID was not recorded beyond `dist-server/index.js` and a `server.info` file. Spark should add an explicit supervisor.
3. **Full OpenMausBot HTTP surface** beyond `/api/bots`, `/api/health`, and `POST /api/bots/{id}/tasks` was not inventoried.
4. **DSH RPC surface** beyond the methods the web UI calls (`session/create`, `session/selectModel`, `session/prompt`, `session/list`) and the cookie/token dance was not fully specified. The UI token lives in DSH artifact logs; there may be a cleaner API.
5. **`review_policy.md` vs live AC / Done behavior** is inconsistent (§3.5). Implement human-only official checkboxes.
6. **DSH MCP 15-minute timeout vs 30–60 minute wait** is an as-built mismatch; the intended resolution is not encoded in yaml comments.
7. **Timezone** (`Asia/Tokyo` vs Asia/Hong_Kong vs naive stamps) is not unified.
8. **Who ticked the farewell ticket’s AC boxes** is not proven.
9. **Fine-grained PAT 403s** are documented from operational history and README notes; this inspection did not re-run GitHub API calls.
10. **DSH provider `api: openai-completions` against a local vLLM/SGLang/Ollama/OpenCode server** was not executed on Spark here. Confirm the DSH provider schema (`dsh --dump-config-schema`) when pointing `baseURL` at localhost.
11. **OpenMausBot’s own OpenRouter/OpenCode local-provider config schema** beyond the `config.json` keys observed (`openrouter.key`, `mcpServers`) was not fully documented. Follow OpenMausBot’s current setup UI/docs on the target machine.
12. **Prototype Superagent GitHub/Feishu runner** may still be configured or paused in DSH state; it is a different loop. This spec does not audit every DSH setting in `~/.dsh/state/superagent`.
13. **Webhook port 8800** appeared in an older `server.info`; Kanban code does not use it.
14. FastMCP **JSON Schema details** (required fields, additionalProperties) are inferred from Python annotations; a reimplementation should run `list_tools` on the stdio servers and treat that dump as the wire contract if anything disagrees with this narrative.

---

# 18. Checklist for a faithful reimplementation

- [ ] Markdown tickets as source of truth; six folder columns; front-matter status mirrors the folder
- [ ] Official AC checkboxes human-only; Done does not tick them
- [ ] MCP names with underscores; DSH prefix `mcp__<server>__<tool>`
- [ ] `bot_send_prompt` wait: default 1800, max 3600, slice 60, `timeout_seconds=None` uses yaml; no `Bot reply: (none)` when tools are running
- [ ] GIT_CONFIG workaround at MCP and git entrypoints; GITHUB_TOKEN re-injection for sync
- [ ] GitHub dry-run without token; classic PAT `repo` + `project` for live Projects v2
- [ ] Permission gate on both Superagent-initiated commands and OpenCode hooks; no force-push
- [ ] Worktree per bot, `.ticket/` and `.opencode/` excluded from commits
- [ ] Web UI invite login, HttpOnly signed cookie, 10/10-min rate limit, 10s refresh, job policy 1 in-flight and max 4/hour
- [ ] Health checks list/check only (no box create)
- [ ] DSH profile: OpenAI-compatible provider, MCP stdio wiring, permission mode explicit
- [ ] OpenMausBot `box` MCP vs any Boat/`BOX_TOKEN` kept distinct
- [ ] Local model endpoint on Spark; no secrets in git; no Vercel secret hosting
- [ ] Existing unit tests plus a small live e2e
- [ ] Stable LAN/reverse-proxy access instead of a 2-hour free tunnel

---

*End of as-built implementation specification. Secret values were excluded by construction.*
