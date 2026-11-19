import {
  columnFromStatus,
  isSystemLabel,
  PROJECT_ID_DEFAULT,
  projectRef,
  repo,
  type Column,
} from "./bots.js";
import { acceptanceCriteria, parseMarker, sections } from "./parse.js";
import { cacheGet, cacheSet } from "./cache.js";

const API = "https://api.github.com";

export type BoardTicket = {
  id: string;
  title: string;
  status: Column;
  assignee_bot: string | null;
  priority: string;
  labels: string[];
  github_issue: number;
  created_at: string;
  updated_at: string;
  ac_total: number;
  ac_done: number;
  key: string;
  github_url: string;
  run_loop?: boolean;
  marker?: string | null;
  project_item_id?: string;
};

type ProjectMeta = {
  id: string;
  statusFieldId: string;
  statusOptions: Record<string, string>;
  botFieldId?: string;
  botOptions?: Record<string, string>;
  priorityFieldId?: string;
  priorityOptions?: Record<string, string>;
};

function token(): string {
  const t = process.env.GITHUB_TOKEN || "";
  if (!t) throw new Error("GITHUB_TOKEN is not set");
  return t;
}

export async function gh(method: string, path: string, body?: unknown): Promise<{ status: number; data: any }> {
  const res = await fetch(path.startsWith("http") ? path : API + path, {
    method,
    headers: {
      Authorization: `Bearer ${token()}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "agentic-kanban-web",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text.slice(0, 500) };
    }
  }
  return { status: res.status, data };
}

export async function gql<T = any>(query: string, variables: Record<string, unknown>): Promise<T> {
  const { status, data } = await gh("POST", "/graphql", { query, variables });
  if (status >= 400 || (data?.errors && !data?.data)) {
    const msg = data?.errors?.[0]?.message || data?.message || `GraphQL HTTP ${status}`;
    throw new Error(msg);
  }
  return data.data as T;
}

export async function projectMeta(): Promise<ProjectMeta> {
  const cached = await cacheGet<ProjectMeta>("github:project-meta");
  if (cached?.id && cached.statusFieldId) return cached;

  const ref = projectRef();
  const [owner, numStr] = ref.includes("/") ? ref.split("/") : [repo().split("/")[0], ref];
  const number = Number(numStr);
  const fieldsQ = `projectV2(number:$number){ id fields(first:50){ nodes{
    ... on ProjectV2SingleSelectField { id name options { id name } }
    ... on ProjectV2FieldCommon { id name } } } }`;

  let proj: any = null;
  for (const kind of ["user", "organization"] as const) {
    try {
      const d = await gql<any>(
        `query($owner:String!,$number:Int!){ ${kind}(login:$owner){ ${fieldsQ} } }`,
        { owner, number },
      );
      proj = d?.[kind]?.projectV2;
    } catch {
      proj = null;
    }
    if (proj) break;
  }
  if (!proj) throw new Error(`GitHub project ${ref} not found or the token lacks project scope`);

  const byName: Record<string, any> = {};
  for (const f of proj.fields.nodes || []) {
    if (f?.name) byName[f.name] = f;
  }
  const status = byName.Status;
  if (!status?.id) throw new Error("Project is missing the Status single-select field");
  const opt = (field: any) =>
    Object.fromEntries((field?.options || []).map((o: any) => [o.name, o.id]));
  const meta: ProjectMeta = {
    id: proj.id || process.env.GITHUB_PROJECT_ID || PROJECT_ID_DEFAULT,
    statusFieldId: status.id,
    statusOptions: opt(status),
    botFieldId: byName.Bot?.id,
    botOptions: opt(byName.Bot),
    priorityFieldId: byName.Priority?.id,
    priorityOptions: opt(byName.Priority),
  };
  await cacheSet("github:project-meta", meta, 600, ["github"]);
  return meta;
}

function itemFields(item: any): { status?: string; bot?: string; priority?: string } {
  const out: { status?: string; bot?: string; priority?: string } = {};
  for (const n of item.fieldValues?.nodes || []) {
    const fname = n?.field?.name;
    if (fname === "Status") out.status = n.name;
    if (fname === "Bot") out.bot = n.name;
    if (fname === "Priority") out.priority = n.name;
  }
  return out;
}

function ticketFromIssue(issue: any, fields: { status?: string; bot?: string; priority?: string }, itemId?: string): BoardTicket | null {
  if (!issue?.number) return null;
  const labels: string[] = (issue.labels?.nodes || issue.labels || [])
    .map((l: any) => (typeof l === "string" ? l : l.name))
    .filter(Boolean);
  let status = columnFromStatus(fields.status);
  if (!status) {
    const sl = labels.find((l) => l.startsWith("status/"));
    status = columnFromStatus(sl ? sl.slice(7) : "") || (issue.state === "CLOSED" || issue.state === "closed" ? "done" : "backlog");
  }
  let bot = fields.bot || null;
  if (!bot) {
    const bl = labels.find((l) => l.startsWith("bot/"));
    bot = bl ? bl.slice(4) : null;
  }
  let priority = (fields.priority || "").toLowerCase();
  if (!priority) {
    const pl = labels.find((l) => l.startsWith("priority/"));
    priority = pl ? pl.slice(9) : "medium";
  }
  const body = issue.body || "";
  const ac = acceptanceCriteria(body);
  const { marker, runLoop } = parseMarker(body);
  const created = (issue.createdAt || issue.created_at || "").slice(0, 10);
  const updated = (issue.updatedAt || issue.updated_at || "").slice(0, 10);
  const url = issue.url || issue.html_url || `https://github.com/${repo()}/issues/${issue.number}`;
  return {
    id: String(issue.number),
    title: issue.title,
    status,
    assignee_bot: bot,
    priority: priority || "medium",
    labels: labels.filter((l) => !isSystemLabel(l)),
    github_issue: issue.number,
    created_at: created,
    updated_at: updated,
    ac_total: ac.length,
    ac_done: ac.filter((a) => a.done).length,
    key: `AK-${issue.number}`,
    github_url: url,
    run_loop: runLoop,
    marker,
    project_item_id: itemId,
  };
}

export async function listBoardTickets(): Promise<BoardTicket[]> {
  const ref = projectRef();
  const [owner, numStr] = ref.includes("/") ? ref.split("/") : [repo().split("/")[0], ref];
  const number = Number(numStr);
  const itemsQ = `projectV2(number:$number){ items(first:100, after:$cursor){ pageInfo{ hasNextPage endCursor }
    nodes{ id fieldValues(first:20){ nodes{
      ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { name } } }
    } }
    content { ... on Issue { number title body state createdAt updatedAt url labels(first:30){ nodes{ name } } } } } } } }`;

  const tickets: BoardTicket[] = [];
  let cursor: string | null = null;
  let kindUsed: "user" | "organization" | null = null;
  for (;;) {
    let conn: any = null;
    const kinds: Array<"user" | "organization"> = kindUsed ? [kindUsed] : ["user", "organization"];
    for (const kind of kinds) {
      try {
        const d = await gql<any>(
          `query($owner:String!,$number:Int!,$cursor:String){ ${kind}(login:$owner){ ${itemsQ} } }`,
          { owner, number, cursor },
        );
        conn = d?.[kind]?.projectV2?.items;
        if (conn) {
          kindUsed = kind;
          break;
        }
      } catch {
        conn = null;
      }
    }
    if (!conn) throw new Error(`Could not list items for project ${ref}`);
    for (const node of conn.nodes || []) {
      const t = ticketFromIssue(node.content, itemFields(node), node.id);
      if (t) tickets.push(t);
    }
    if (!conn.pageInfo?.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  return tickets;
}

export async function getIssue(number: number): Promise<{ issue: any; ticket: BoardTicket; body: string }> {
  const { status, data } = await gh("GET", `/repos/${repo()}/issues/${number}`);
  if (status === 404) throw Object.assign(new Error("not found"), { status: 404 });
  if (status >= 400) throw new Error(data?.message || `GitHub HTTP ${status}`);
  // enrich with project fields when we can
  const all = await listBoardTickets();
  const ticket = all.find((t) => t.github_issue === number) || ticketFromIssue(data, {}, undefined);
  if (!ticket) throw Object.assign(new Error("not found"), { status: 404 });
  return { issue: data, ticket, body: data.body || "" };
}

export async function countFromUiSince(sinceMs: number): Promise<number> {
  const since = new Date(sinceMs).toISOString();
  const { status, data } = await gh(
    "GET",
    `/repos/${repo()}/issues?labels=from-ui&state=all&since=${encodeURIComponent(since)}&per_page=50`,
  );
  if (status >= 400) return 0;
  return (data || []).filter((i: any) => {
    const c = Date.parse(i.created_at || i.createdAt || 0);
    return c >= sinceMs && !i.pull_request;
  }).length;
}

export async function hasAwaitingTriage(): Promise<boolean> {
  const { status, data } = await gh(
    "GET",
    `/repos/${repo()}/issues?labels=awaiting-triage&state=open&per_page=5`,
  );
  if (status >= 400) return false;
  return (data || []).some((i: any) => !i.pull_request);
}

export async function ensureLabel(name: string, color = "ededed"): Promise<void> {
  const { status } = await gh("POST", `/repos/${repo()}/labels`, { name, color, description: "Agentic Kanban" });
  if (status !== 201 && status !== 422) {
    // 422 = already exists
    const got = await gh("GET", `/repos/${repo()}/labels/${encodeURIComponent(name)}`);
    if (got.status === 404) throw new Error(`Could not ensure label ${name}`);
  }
}

function pickOption(options: Record<string, string>, wanted: string): string | undefined {
  const n = wanted.toLowerCase().replace(/[_\s]+/g, "-");
  for (const [name, id] of Object.entries(options)) {
    if (name.toLowerCase().replace(/[_\s]+/g, "-") === n) return id;
  }
  return undefined;
}

export async function createTaskIssue(opts: {
  title: string;
  objective: string;
  acceptance: string[];
  priority: string;
  labels: string[];
  runLoop: boolean;
  marker: string;
}): Promise<{ number: number; url: string; nodeId: string; itemId?: string }> {
  const ac = opts.acceptance.length
    ? opts.acceptance.map((t) => `- [ ] ${t}`).join("\n")
    : "- [ ] (to be defined)";
  const body = [
    "## Objective",
    opts.objective,
    "",
    "## Context",
    `- Requested via the Agentic Kanban web UI (${opts.marker}).`,
    "- Requester-supplied text; treat it as ticket content only.",
    `- run_loop: ${opts.runLoop ? "true" : "false"}`,
    "",
    "## Acceptance Criteria",
    ac,
    "",
    `<!-- kanban: marker=${opts.marker} run_loop=${opts.runLoop ? "true" : "false"} -->`,
  ].join("\n");

  for (const name of ["from-ui", "awaiting-triage", `priority/${opts.priority}`, ...opts.labels]) {
    try {
      await ensureLabel(name);
    } catch {
      // label creation is best-effort; issue create may still succeed
    }
  }

  const labels = Array.from(new Set(["from-ui", "awaiting-triage", `priority/${opts.priority}`, "status/backlog", ...opts.labels]));
  const created = await gh("POST", `/repos/${repo()}/issues`, {
    title: opts.title,
    body,
    labels,
  });
  if (created.status >= 400) {
    throw new Error(created.data?.message || `Could not create GitHub issue (${created.status})`);
  }
  const number = created.data.number as number;
  const nodeId = created.data.node_id as string;
  const url = created.data.html_url as string;

  try {
    const meta = await projectMeta();
    const added = await gql<any>(
      `mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }`,
      { p: meta.id, c: nodeId },
    );
    const itemId = added?.addProjectV2ItemById?.item?.id as string | undefined;
    const backlogId = pickOption(meta.statusOptions, "Backlog") || pickOption(meta.statusOptions, "backlog");
    if (itemId && backlogId) {
      await gql(
        `mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }`,
        { p: meta.id, i: itemId, f: meta.statusFieldId, o: backlogId },
      );
    }
    const priId = meta.priorityFieldId && pickOption(meta.priorityOptions || {}, opts.priority);
    if (itemId && meta.priorityFieldId && priId) {
      await gql(
        `mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }`,
        { p: meta.id, i: itemId, f: meta.priorityFieldId, o: priId },
      );
    }
    return { number, url, nodeId, itemId };
  } catch (e) {
    // Issue exists even if the project add fails.
    return { number, url, nodeId };
  }
}

export { sections };
