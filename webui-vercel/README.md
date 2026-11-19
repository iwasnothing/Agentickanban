> **Unused leftover.** This Vercel app and the box-side `scripts/kanban_bridge.py` poller
> are from a cancelled hosting plan. The supported UI is the local FastAPI app in `webui/`
> (see the top-level SETUP.md). Leave this folder and the bridge stopped unless you are
> deliberately reviving that path.

# Agentic Kanban (Vercel web UI)

Jira-like board for the Kanban bots system. This app is the **public website**. Superagent, OpenMausBot and the Simular VM stay on the box. They talk to this app through GitHub Issues (the task bus) and a short authenticated heartbeat.

Do not put secret values in this directory. Set them in the Vercel project settings and, on the box, in `/workspace/.secrets/` files (mode 600).

## Architecture

```
Browser ──invite code──► Vercel app (this folder)
                           │
                           ├─ Board / ticket detail  ← GitHub Issues + Project iwasnothing/2
                           ├─ Create task            → opens an issue (from-ui, awaiting-triage), adds it to the project (Backlog, unassigned)
                           ├─ Superagent / OpenMausBot health ← last heartbeat in Vercel Runtime Cache
                           └─ Simular VM health      ← read-only box_list via Simular MCP (never creates or wakes a box)

Box poller (scripts/kanban_bridge.py, tmux kanban-bridge)
  every ~15s: POST /api/heartbeat (BRIDGE_SECRET)
              list issues labelled awaiting-triage
              import the existing issue into the local ticket store (no second issue)
              drive Superagent to triage / sync, optionally run the bot loop
```

Runtime Cache stores heartbeats, job status and login rate-limit counters. If Runtime Cache is unavailable (for example `vercel dev` off-platform), the app falls back to in-process memory. That fallback is **fragile**: it is lost on cold starts and is not shared across instances.

## Deploy on Vercel

1. Create a new Vercel project. If this folder lives inside the `ai-bot-system` monorepo, set the **Root Directory** to `webui-vercel`. Framework preset: **Other**. Node.js 22 or 24.
2. Set the environment variables below (names only here — values stay in the Vercel dashboard). Apply them to Production (and Preview if you want).
3. Deploy. Copy the production URL into `/workspace/ai-bot-system/.state/kanban-vercel-url.txt` on the box (a single `https://…` line, no trailing commentary).
4. Start the box poller (see below). It will not contact Vercel while that file still holds the placeholder `https://YOUR-VERCEL-APP.vercel.app`.

This repo’s existing local UI (`webui/`, tmux `kanban-webui` / `kanban-tunnel`) is separate and should be left running.

### Environment variables on Vercel

| Name | Required | Purpose |
|---|---|---|
| `KANBAN_INVITE_CODE` | yes | Invite-code login (the same code as the local demo UI) |
| `SESSION_SECRET` | yes | Signs the HttpOnly session cookie. Generate a long random string and store it only in the Vercel project settings (never in git) |
| `BRIDGE_SECRET` | yes | Shared secret for the box poller (`Authorization: Bearer …` on `/api/heartbeat` and `POST /api/jobs`) |
| `GITHUB_TOKEN` | yes | PAT or GitHub App token with Issues read/write and Projects read/write on `iwasnothing/Agentickanban` and project `iwasnothing/2` |
| `GITHUB_REPO` | yes | `iwasnothing/Agentickanban` |
| `GITHUB_PROJECT` | yes | `iwasnothing/2` |
| `SIMULAR_BOX_TOKEN` | yes | Server-side only; used for the read-only Simular `box_list` health check |
| `GITHUB_PROJECT_ID` | no | Defaults to `PVT_kwHOAlPnVM4BrZEC` if lookup fails |
| `SIMULAR_BOX_ID` | no | Defaults to `cb-00a9be6c1672` |
| `KANBAN_MAX_TASKS_PER_HOUR` | no | Defaults to `4` |

Never commit those values. Never print them in logs.

### Environment on the box (poller)

The start script loads `/workspace/.secrets/kanban-vercel.env` (for `BRIDGE_SECRET`) and `/workspace/.secrets/kanban-webui.env` / `/workspace/.secrets/github-token` as needed, and reads the public URL from `KANBAN_VERCEL_URL` or `.state/kanban-vercel-url.txt`.

| Name | Required | Purpose |
|---|---|---|
| `KANBAN_VERCEL_URL` | yes* | Public Vercel origin (`https://…`). *Or the URL file above |
| `BRIDGE_SECRET` | yes | Must match the Vercel value |
| `GITHUB_TOKEN` | yes | Same repo access; used to list `awaiting-triage` issues and to drop that label after triage starts |
| `GITHUB_REPO` | no | Defaults to `iwasnothing/Agentickanban` |
| `GITHUB_PROJECT` | no | Defaults to `iwasnothing/2` |

The Superagent process already has its OpenRouter key; the poller does not need a second copy.

## Start / stop the box poller

```bash
# after the Vercel URL is written to .state/kanban-vercel-url.txt
tmux new-session -d -s kanban-bridge -c /workspace/ai-bot-system \
  "/workspace/ai-bot-system/scripts/start_kanban_bridge.sh 2>&1 | tee -a /workspace/ai-bot-system/.state/kanban-bridge.log"

tmux kill-session -t kanban-bridge
```

Logs: `.state/kanban-bridge.log` and `.state/kanban-bridge-jobs.json`. The poller does not replace or stop `kanban-webui` or `kanban-tunnel`.

## Smoke checklist (after you deploy)

Do these yourself; this tree was not deployed from the box.

1. Open the Vercel URL unauthenticated: `/` should end up on `/login`. `/api/board`, `/api/health`, `/api/jobs` and `POST /api/tasks` should return **401**.
2. Sign in with the invite code. A wrong code should be 401. More than 10 tries from one IP in 10 minutes should be 429.
3. The board should list the public project issues as cards (AK-N), in status columns and bot / Unassigned rows. Click a card and confirm objective, acceptance checkboxes (unchecked unless a human ticked them on GitHub), and the GitHub link.
4. Health badges: Simular VM should resolve via `box_list` (green if running, amber if hibernated). Superagent and OpenMausBot stay red/amber until the box poller posts heartbeats.
5. Start the poller, wait ~15 seconds, refresh: Superagent and OpenMausBot should go green if those processes are up on the box.
6. Create-task (optional, live): submit a clearly labelled dry-run title, confirm a new GitHub issue appears with `from-ui` + `awaiting-triage`, is on the project in Backlog, and that the poller imports it (no second issue) and removes `awaiting-triage` after triage starts. Close or finish that issue when you are done.
7. A second submit while a job is in flight, or a fifth submit in an hour, should return the existing 429 messages.

Acceptance checkboxes on cards are never auto-ticked by this app.

## Local preview (optional)

```bash
cd webui-vercel
npx vercel dev
```

Runtime Cache will not be available; health and rate limits use the in-memory fallback.
