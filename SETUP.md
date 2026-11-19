# Setup: Agentic Kanban with DeepSeek Superagent and OpenMausBot

This guide stands up the stack on a fresh Linux machine (including a DGX Spark-class box): official DeepSeek Harness + the dsh-superagent prototype, an OpenMausBot headless server, and this control-plane repository (tickets, MCP servers, permission gate, worker watchdog, local web UI).

It is written for a competent operator. Official DeepSeek Superagent (DSH) install commands are taken from the upstream `SETUP.md` and `docs/006-deployment.md`. Official OpenMausBot commands are taken from its README and `docs/headless-cli.md`. Do not skip those documents if something here conflicts with a newer upstream release — but **pin the two official checkouts to the SHAs below** for a layout that matches this control plane.

Secrets (API keys, GitHub tokens, invite codes, session keys) are never stored in git. Create them as mode-600 files outside the repositories, or export them in the shell that starts the services. This guide names environment variables and example paths only.

## 1. What you are installing

| Piece | Role | Source |
|---|---|---|
| This repository (`Agentickanban`) | Control plane: Markdown tickets, MCP servers, permission gate, watchdog / risk approval, local FastAPI UI | https://github.com/iwasnothing/Agentickanban (MIT; see `LICENSE` and `NOTICE`) |
| `dsh-superagent-prototype` | DeepSeek Superagent manager (research prototype, not an official DeepSeek product) | https://github.com/deepseek-ai/dsh-superagent-prototype (MIT) |
| DeepSeek Harness (`dsh`) | Runtime that loads the Superagent profile and serves the panel on `127.0.0.1:3082` | `@deepseek-ai/dsh` (see pinned version below) |
| `OpenMausBot` | Worker-bot harness, headless on `127.0.0.1:8799` | https://github.com/milind-soni/OpenMausBot (Apache 2.0, with an `enterprise/` license carve-out; this stack uses the OSS / headless path only) |

**Do not vendor or copy** the DSH or OpenMausBot trees into this repository. Clone them as siblings.

### Pinned upstream revisions

| Repository | Pinned commit |
|---|---|
| [deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype) | `8c32a9dc5d72c033816c6a8110b37f40cf94fba4` |
| [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) | `2d74ba9616270b4a1df1ebfa4a912b55bd5cb4ea` |

DeepSeek Harness version used with that prototype (from upstream `docs/006-deployment.md`): **`@deepseek-ai/dsh@0.2.1-rc.3`**. Other Harness versions must satisfy `engines.dsh` in the prototype's `package.json` (`^0.2.1-alpha.2`).

Engine ranges (from the official `package.json` files at those SHAs):

- DSH prototype: Node.js `^22.19.0 || >=24.0.0`, `pnpm@11.7.0`
- OpenMausBot: Node.js `>=24`, `pnpm@10.33.0`

Install **Node.js 24+** so one runtime covers both. Python **3.10+** is enough for this control plane (`requirements.txt`: `mcp>=1.10,<2`, `pyyaml>=6`, `pytest>=8`). Upstream DSH also lists git 2.32+, tmux, and GitHub CLI 2.48+.

### Licenses and attribution

- **This repo:** MIT (`LICENSE`).
- **dsh-superagent-prototype:** MIT. Upstream describes it as a research prototype for reference only.
- **OpenMausBot:** Apache License 2.0, except `enterprise/`, which is source-available under the OpenMausBot Enterprise License. Deleting `enterprise/` leaves the open-source edition. This control plane talks to the headless OSS server (`openmausbot serve` / `pnpm dev:server`) and does not require the enterprise folder. See OpenMausBot's `LICENSE`, `LICENSING.md`, and `NOTICE`.
- Full pointer: `NOTICE` in this repository. Do not relicense upstream code.

## 2. Recommended directory layout

Keep the three git checkouts as siblings, and keep secrets *outside* every git repo:

```text
$KANBAN_ROOT/                          # e.g. ~/kanban or /opt/kanban
  Agentickanban/                       # this control-plane repo
  dsh-superagent-prototype/            # official, pinned SHA
  OpenMausBot/                         # official, pinned SHA
  aibots/
    project-repo/                      # git repo the worker bots edit
    workspaces/                        # per-bot worktrees (created later)

$KANBAN_SECRETS_DIR/                   # e.g. ~/.config/kanban/secrets  (mode 700)
  openrouter-user.key                  # mode 600, OpenRouter API key only
  github-token                         # mode 600, optional
  kanban-webui.env                     # mode 600, optional env file
  kanban-webui-session.key             # mode 600, created on first UI start if missing
```

On the reference machine the same layout lives under `/workspace` (control repo path `/workspace/ai-bot-system`, secrets `/workspace/.secrets`). Start scripts and `superagent/config/superagent.yaml` still default to those paths so that machine keeps working, but every path can be overridden with environment variables. Set the variables in the shell or tmux session that starts the services:

| Variable | Meaning | Reference default |
|---|---|---|
| `KANBAN_ROOT` | Parent of the sibling checkouts | parent of the control repo |
| `AIBOTS_CONTROL_ROOT` | This control-plane checkout | `/workspace/ai-bot-system` |
| `DSH_SUPERAGENT_ROOT` | Official DSH prototype checkout | `/workspace/dsh-superagent-prototype` |
| `OPENMAUSBOT_ROOT` | Official OpenMausBot checkout | `/workspace/OpenMausBot` |
| `KANBAN_SECRETS_DIR` | Mode-700 directory for secret files | `/workspace/.secrets` |
| `AIBOTS_PROJECT_REPO` | Git repo the workers edit | `/workspace/aibots/project-repo` |
| `AIBOTS_WORKSPACES_ROOT` | Per-bot worktrees | `/workspace/aibots/workspaces` |
| `OPENROUTER_KANBAN_KEY_FILE` | Mode-600 OpenRouter key file | `$KANBAN_SECRETS_DIR/openrouter-user.key` |
| `GITHUB_TOKEN_FILE` | Mode-600 GitHub token file | `$KANBAN_SECRETS_DIR/github-token` |
| `DSH_BIN` | `dsh` executable | first `dsh` on `PATH`, else `/home/box/.local/bin/dsh` |

A complete list of optional variables is in `.env.example` (empty values; copy it out of git if you fill it in). `scripts/config.py` also honours `AIBOTS_STATE_DIR`, `AIBOTS_AUTO_COMMIT`, and `AIBOTS_GITHUB_SYNC`.

## 3. Prerequisites

On Ubuntu-like Linux (including a DGX Spark-class host):

1. **Node.js 24+** and `npm`. Put a user-writable npm prefix on `PATH` if you do not install packages as root, for example:

   ```sh
   mkdir -p "$HOME/.local"
   npm config set prefix "$HOME/.local"
   export PATH="$HOME/.local/bin:$PATH"
   ```

   Persist the `PATH` change in your shell profile.
2. **pnpm.** Upstream DSH declares `pnpm@11.7.0`. Corepack (`corepack enable && corepack prepare pnpm@11.7.0 --activate`) or a standalone pnpm install both work. OpenMausBot declares `pnpm@10.33.0`; a current pnpm 10.x or 11.x is what the reference install used.
3. **git 2.32+**, **tmux**, **Python 3.10+**, **curl**.
4. **GitHub CLI (`gh`) 2.48+** — required by official DSH setup, optional if you only use this control plane's Markdown tickets and skip live GitHub sync.
5. Disk and memory: the Node checkouts and `pnpm install` need several gigabytes; a DGX-class machine is more than enough. Bind every service in this guide to **127.0.0.1**.

Official OpenMausBot *desktop* packages are documented for Ubuntu 24.04 x64. On aarch64 (some Spark-class machines) use the **from-source / headless** path below rather than the `.deb`.

## 4. Clone the repositories

```sh
export KANBAN_ROOT="${KANBAN_ROOT:-$HOME/kanban}"
mkdir -p "$KANBAN_ROOT"
cd "$KANBAN_ROOT"
git clone https://github.com/iwasnothing/Agentickanban.git
# Optional: if this repo is already checked out elsewhere, set AIBOTS_CONTROL_ROOT to it instead.
export AIBOTS_CONTROL_ROOT="$KANBAN_ROOT/Agentickanban"
"$AIBOTS_CONTROL_ROOT/scripts/bootstrap_deps.sh"
```

`bootstrap_deps.sh` clones the two official repos next to the control plane at the pinned SHAs. If a destination already exists, it prints the current HEAD and **does not** reset or re-clone (safe on a machine that already runs the stack).

To clone by hand instead:

```sh
git clone https://github.com/deepseek-ai/dsh-superagent-prototype.git "$KANBAN_ROOT/dsh-superagent-prototype"
git -C "$KANBAN_ROOT/dsh-superagent-prototype" checkout --detach 8c32a9dc5d72c033816c6a8110b37f40cf94fba4
git clone https://github.com/milind-soni/OpenMausBot.git "$KANBAN_ROOT/OpenMausBot"
git -C "$KANBAN_ROOT/OpenMausBot" checkout --detach 2d74ba9616270b4a1df1ebfa4a912b55bd5cb4ea
```

## 5. Install DeepSeek Harness and the Superagent prototype

Follow upstream `dsh-superagent-prototype/docs/006-deployment.md` (Install, Machine configuration, Start and stop). The install block, for a machine that does **not** already have a `superagent` profile:

```sh
npm install -g @deepseek-ai/dsh@0.2.1-rc.3
cd "$KANBAN_ROOT/dsh-superagent-prototype"
pnpm install && pnpm build
dsh superagent --from-default-profile web --dump-config > /dev/null
dsh plugin --profile superagent add "link:$PWD"
```

The fourth command creates `~/.dsh/profiles/superagent` from the Harness Web template. The fifth installs the prototype bundle (`package.json` will `link:` the checkout you ran it from). Set `DSH_HOME` if you want `~/.dsh` somewhere else.

**Do not** run `dsh superagent --from-default-profile` again on an existing profile: it replaces the profile and **deletes** `cordis.patch.yml`.

Official DSH also documents an agent GitHub account and optional Feishu source. Those are for DSH's own ticket runner. This control plane's manager loop uses MCP tools against Markdown tickets and does not require Feishu. You can complete or defer the official Sources / Policy panel steps; you still need a configured **model provider** (next section).

A model provider is required. This stack uses OpenRouter and the free model `nvidia/nemotron-3-ultra-550b-a55b:free`, wired in the profile patch (section 9). Create an OpenRouter key in your own terminal and store it only in the secret file from section 8. Never paste a key into a chat, a ticket, or a git-tracked file.

## 6. Install OpenMausBot (headless, loopback)

Two supported ways; both listen on **127.0.0.1:8799**.

### A. Global CLI (what the reference deployment runs)

```sh
npm install -g openmausbot
# First-time provider / engine setup is interactive — run this in your own terminal, not in a shared log:
#   openmausbot setup
# Headless, no browser, no pairing prompt, ignore any saved remote-access settings:
openmausbot serve --local --no-open --no-pair --port 8799
```

`--local` and `--no-pair` are documented in the official CLI help and `docs/headless-cli.md`. The reference process is `openmausbot serve --local --no-open --no-pair` (port defaults to 8799).

A globally installed CLI may be a newer package version than the pinned git SHA. For a layout that matches the checked-out tree, prefer option B, or record `openmausbot --version` after the install.

### B. From the pinned source checkout

```sh
cd "$KANBAN_ROOT/OpenMausBot"
pnpm install
pnpm dev:server    # harness server → 127.0.0.1:8799
```

That is the official "from source" command in OpenMausBot's README. You can later install the CLI from this checkout if you want `openmausbot` on `PATH` for MCP (`openmausbot mcp`) and the control plane's `openmausbot.cli` setting.

### Worker coding engine

OpenMausBot needs at least one coding engine. Official docs mention `claude`, `codex`, or `grok`. The reference deployment uses **OpenCode** on `PATH`, with the worker model in `bots/registry.yaml` set to an OpenRouter free model (`opencodeGo/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`). Install and sign in an engine in your own terminal; see OpenMausBot `docs/custom-engines.md` and `docs/cli-onboarding.md`. Do not put engine keys in this repository.

Optional: `openmausbot browser install` if workers need a browser. The reference notes this as optional.

## 7. Install this control plane

```sh
export AIBOTS_CONTROL_ROOT="${AIBOTS_CONTROL_ROOT:-$KANBAN_ROOT/Agentickanban}"
cd "$AIBOTS_CONTROL_ROOT"

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

python3 -m venv webui/.venv
webui/.venv/bin/pip install -U pip
webui/.venv/bin/pip install -r webui/requirements.txt

.venv/bin/python -m pytest -q
```

Edit `superagent/config/superagent.yaml` so `paths.*` and `openmausbot.cli` match this machine (or set the env vars in the table in section 2 and leave the file's `/workspace/...` defaults alone on the reference box). You do not need to remove the reference defaults: env vars win for the roots that `scripts/config.py` and the start scripts honour (`AIBOTS_CONTROL_ROOT`, `AIBOTS_PROJECT_REPO`, `AIBOTS_WORKSPACES_ROOT`, `AIBOTS_STATE_DIR`). The YAML `openmausbot.cli` path should still be edited to your `openmausbot` binary (commonly `$HOME/.local/bin/openmausbot`).

Create the worker project repo and worktree root if they do not exist yet (any small git repo is enough):

```sh
export AIBOTS_PROJECT_REPO="${AIBOTS_PROJECT_REPO:-$KANBAN_ROOT/aibots/project-repo}"
export AIBOTS_WORKSPACES_ROOT="${AIBOTS_WORKSPACES_ROOT:-$KANBAN_ROOT/aibots/workspaces}"
mkdir -p "$AIBOTS_PROJECT_REPO" "$AIBOTS_WORKSPACES_ROOT"
if [[ ! -d "$AIBOTS_PROJECT_REPO/.git" ]]; then
  git -C "$AIBOTS_PROJECT_REPO" init
  # add a README or your application code, then commit
fi
```

Register a worker after OpenMausBot is running:

1. Create an OpenMausBot bot (`openmausbot new-bot`, or the OpenMausBot UI/MCP `create_bot` tool) and note its id.
2. Copy `bots/bot-001/` or edit it. Put the OpenMausBot bot id, workspace path, and branch in `bots/registry.yaml`.
3. Run `scripts/workspace/create_worktree.sh bot-001` (it honours `AIBOTS_PROJECT_REPO` and `AIBOTS_WORKSPACES_ROOT`). This creates `bots/<id>` on the project repo and installs the permission-gate OpenCode plugin.

## 8. Secret files (create empty / example instructions only)

Create a directory that is **not** inside a git working tree:

```sh
export KANBAN_SECRETS_DIR="${KANBAN_SECRETS_DIR:-$HOME/.config/kanban/secrets}"
mkdir -p "$KANBAN_SECRETS_DIR"
chmod 700 "$KANBAN_SECRETS_DIR"
```

Create mode-600 files yourself and put **only** the secret value in each file (no `KEY=` prefix, no quotes). Do not invent or paste real keys into tickets, docs, or this repo.

| File | Contents | Used by |
|---|---|---|
| `$KANBAN_SECRETS_DIR/openrouter-user.key` | OpenRouter API key only | `scripts/start_dsh_superagent.sh` (`OPENROUTER_KANBAN_KEY_FILE`) |
| `$KANBAN_SECRETS_DIR/github-token` | GitHub PAT or app token, optional | start scripts / MCP (`GITHUB_TOKEN_FILE`). Without it, GitHub sync dry-runs. |
| `$KANBAN_SECRETS_DIR/kanban-webui.env` | Optional `export`-style file (mode 600) with `KANBAN_INVITE_CODE=...` and, if you use it, `SIMULAR_BOX_TOKEN=...` | `scripts/start_kanban_webui.sh` |

You can also export `KANBAN_INVITE_CODE` in the environment instead of a file. The local web UI will generate a session key file (`KANBAN_WEBUI_SESSION_KEY_FILE`, default `$KANBAN_SECRETS_DIR/kanban-webui-session.key`) on first start if it is missing.

`.env.example` at the repo root lists the variable names. If you fill one in, keep that copy outside git (`*.env` is ignored; `.env.example` is the only exception).

## 9. Wire the DSH Superagent profile

1. Back up `~/.dsh/profiles/superagent/cordis.patch.yml` if it already exists.
2. Copy `deploy/dsh/cordis.patch.yml.example` over it, **or merge** the OpenRouter provider, default model, and the three MCP server blocks into the existing patch.
3. Replace every `/path/to/...` placeholder with absolute paths:
   - `stateDir` — e.g. `$HOME/.dsh/state/superagent`
   - `command` for OpenMausBot MCP — your `openmausbot` binary
   - `command` for the Kanban MCP servers — `$AIBOTS_CONTROL_ROOT/scripts/start_kanban_mcp.sh`
   - `AIBOTS_CONTROL_ROOT`, `GITHUB_REPO`, `GITHUB_PROJECT` in the MCP `env` blocks
4. Leave `apiKeyEnv: OPENROUTER_KANBAN_API_KEY`. The start script loads the key file into that variable. Do not write the key into the YAML.
5. The default model in the example is `nvidia/nemotron-3-ultra-550b-a55b:free`. Keep `toolCallTimeoutMs` as in the example (bot turns can run up to an hour).

The three MCP servers registered in the profile are `mcp-openmausbot`, `mcp-kanban-tickets`, and `mcp-kanban-bots`. Restart DSH after any profile change (section 10).

## 10. Start the tmux sessions

Export the path and key-file variables in the shell first, then start sessions in this order. OpenMausBot and the key file must be in place before DSH if you want the manager tools to work immediately.

```sh
export AIBOTS_CONTROL_ROOT="${AIBOTS_CONTROL_ROOT:-$KANBAN_ROOT/Agentickanban}"
export DSH_SUPERAGENT_ROOT="${DSH_SUPERAGENT_ROOT:-$KANBAN_ROOT/dsh-superagent-prototype}"
export KANBAN_SECRETS_DIR="${KANBAN_SECRETS_DIR:-$HOME/.config/kanban/secrets}"
export OPENROUTER_KANBAN_KEY_FILE="$KANBAN_SECRETS_DIR/openrouter-user.key"
export AIBOTS_PROJECT_REPO="${AIBOTS_PROJECT_REPO:-$KANBAN_ROOT/aibots/project-repo}"
export AIBOTS_WORKSPACES_ROOT="${AIBOTS_WORKSPACES_ROOT:-$KANBAN_ROOT/aibots/workspaces}"
# optional:
# export GITHUB_TOKEN_FILE="$KANBAN_SECRETS_DIR/github-token"
# export GITHUB_REPO=owner/repo
# export GITHUB_PROJECT=owner/NUMBER
# export KANBAN_WEBUI_ENV_FILE="$KANBAN_SECRETS_DIR/kanban-webui.env"
```

**OpenMausBot** (headless, loopback). The reference deployment runs the CLI outside tmux; a tmux session is a good idea on a fresh box:

```sh
# CLI install:
tmux new-session -d -s openmausbot "openmausbot serve --local --no-open --no-pair --port 8799"
# or from the pinned checkout:
# tmux new-session -d -s openmausbot -c "$KANBAN_ROOT/OpenMausBot" "pnpm dev:server"
```

**DeepSeek Superagent** — use this repo's wrapper. It reads the OpenRouter key from the key file (never from the command line), exports it, tees the panel log into `$DSH_SUPERAGENT_ROOT/.artifacts`, and starts the worker watchdog session if it is not already running:

```sh
tmux new-session -d -s dsh-superagent "$AIBOTS_CONTROL_ROOT/scripts/start_dsh_superagent.sh"
```

Official alternative, if you prefer upstream's helper: from the DSH checkout, `scripts/dsh-superagent start` (tmux session `dsh-superagent`). You must still export `OPENROUTER_KANBAN_API_KEY` / `OPENROUTER_API_KEY` in that environment, and start the watchdog yourself (next command).

**Worker watchdog** (started automatically by `start_dsh_superagent.sh`; this is the manual form):

```sh
tmux new-session -d -s kanban-watchdog "$AIBOTS_CONTROL_ROOT/scripts/start_worker_watchdog.sh"
```

The watchdog inspects configured workers for stalls (host-permission waits, permission-gate blocks, hung turns). It may approve **low-risk** pending commands, such as `pip install` of well-known data libraries (for example `plotly` or `xlrd`) from the default index, and it rejects higher-risk requests with a counter-proposal. Official acceptance checkboxes on tickets stay unchecked.

**Local web UI** (FastAPI, `127.0.0.1:8787`):

```sh
mkdir -p "$AIBOTS_CONTROL_ROOT/.state"
tmux new-session -d -s kanban-webui   "bash -c '$AIBOTS_CONTROL_ROOT/scripts/start_kanban_webui.sh 2>&1 | tee -a $AIBOTS_CONTROL_ROOT/.state/webui-server.log'"
```

**Optional localtolink tunnel** for a temporary public HTTPS URL in front of the local UI. This is optional, the URL changes when the tunnel restarts, and it is not required for loopback use. The helper is `scripts/start_kanban_tunnel.sh` (tmux session `kanban-tunnel`). Install the `localtolink` CLI under `webui/tunnel/` if you use it. Do not commit the URL file (gitignored).

**Not required:** `scripts/start_kanban_bridge.sh` and `webui-vercel/` are leftovers from a cancelled Vercel hosting plan. Leave them stopped unless you are deliberately reviving that path.

The DSH panel URL (including its token) is written to the newest `$DSH_SUPERAGENT_ROOT/.artifacts/dsh-superagent-superagent-*.log`. Keep those logs private (`.artifacts/` is gitignored).

## 11. Verify

```sh
# Local web UI
curl -sS http://127.0.0.1:8787/healthz
# expected: {"ok":true}

# DeepSeek Superagent panel
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3082
# a 2xx/3xx response means the web server is up.
# From the official checkout you can also run:
#   "$DSH_SUPERAGENT_ROOT/scripts/dsh-superagent" --profile superagent status

# OpenMausBot
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8799
openmausbot status     # or: openmausbot doctor --host

# Create a ticket in the control plane
cd "$AIBOTS_CONTROL_ROOT"
bin/ticket create "Add a smoke-test greeting option" \
  -o "Add a trivial, testable option to the demo project" \
  -a "The option is implemented" -a "Tests pass" -l python
bin/ticket list
```

Open the local UI at http://127.0.0.1:8787 (invite code login). Open the DSH panel at http://127.0.0.1:3082 (token from the artifacts log) and confirm the Superagent session can see the `kanban-tickets`, `kanban-bots`, and `openmausbot` MCP tools.

A scripted loop that does not need the LLM is `.venv/bin/python scripts/e2e_smoke.py` (creates a ticket, talks to OpenMausBot, reviews, and dry-runs GitHub sync unless a token is set). Use it only against a disposable project repo.

## 12. Day-to-day notes

- Restart DSH after profile changes: stop the `dsh-superagent` tmux session (or use official `scripts/dsh-superagent restart`) and start `start_dsh_superagent.sh` again with the same environment.
- Approve a gated command by hand: `bin/gate approve --bot bot-001 -- "pip install requests"`. Check first with `bin/gate check --bot bot-001 -- "<cmd>"`.
- Run the watchdog once without applying: `bin/watchdog once --no-apply`.
- Tests: `.venv/bin/python -m pytest -q`.
- GitHub sync: set `github.repo` / `github.project` in `superagent.yaml` or `GITHUB_REPO` / `GITHUB_PROJECT`. Markdown tickets stay authoritative.
- Official DSH hourly-check and upgrade procedures stay in upstream `docs/006-deployment.md`.
- More operation detail (MCP tool names, sandbox, runbooks) is in `README.md`.

## 13. Optional leftovers (not required)

- `webui-vercel/` and `scripts/kanban_bridge.py` — cancelled Vercel hosting plan. If you keep the folder in a checkout, treat it as unused.
- `deploy/docker-compose.yml` and `deploy/sandbox.Dockerfile` — written for a later containerised sandbox; the reference machine does not use Docker. Today's isolation is one git worktree per bot plus the permission gate.
