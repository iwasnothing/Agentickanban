# Superagent (manager)

DeepSeek Superagent (DSH, official `dsh-superagent-prototype` checkout, profile
`~/.dsh/profiles/superagent/cordis.patch.yml`) is the manager. It reaches the
system only through MCP. Install and profile wiring: see the top-level [SETUP.md](../SETUP.md).

| MCP server | DSH tool prefix | Purpose |
|---|---|---|
| `kanban-tickets` (`mcp_servers/ticket_mcp.py`) | `mcp__kanban-tickets__*` | create/get/update/move/assign/list tickets, review notes, triage |
| `kanban-bots` (`mcp_servers/bots_mcp.py`) | `mcp__kanban-bots__*` | bot.* tools: sessions, prompts, workspace files, diff, commit, tests |
| `openmausbot` (`openmausbot mcp`) | `mcp__openmausbot__*` | raw OpenMausBot control (list bots, messages, models) |

- `config/superagent.yaml` – paths, OpenMausBot URL, GitHub env var names, loop limits
- `config/triage_rules.yaml` – rule-based triage weights (`ticket_triage`)
- `config/review_policy.md` – what every review must do
- `prompts/` – triage / review / gap analysis prompts (also served as MCP prompts by `kanban-tickets`)

The manager loop: intake -> triage -> (planning) -> dispatch -> review/gap
analysis -> iterate -> in-review/done. See the top-level README runbooks.
