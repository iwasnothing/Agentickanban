export const COLUMNS = [
  "backlog",
  "to-do",
  "planning",
  "in-progress",
  "in-review",
  "done",
] as const;

export type Column = (typeof COLUMNS)[number];

/** Snapshot of bots/registry.yaml — the Vercel app cannot read the box filesystem. */
export const BOTS = [
  {
    id: "bot-001",
    name: "Backend Python bot",
    omb_name: "MausBot",
    omb_id: "8a7348b7-596f-4a9a-8c13-fa57a262ceaf",
    model: "opencodeGo/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    branch: "bots/bot-001",
    enabled: true,
  },
];

export const REPO_DEFAULT = "iwasnothing/Agentickanban";
export const PROJECT_DEFAULT = "iwasnothing/2";
export const PROJECT_ID_DEFAULT = "PVT_kwHOAlPnVM4BrZEC";
export const BOX_ID_DEFAULT = "cb-00a9be6c1672";
export const BOX_MCP = "https://mcp.box.simular.ai/mcp";
export const COOKIE = "ak_session";
export const SESSION_MAX_AGE = 7 * 24 * 3600;
export const MAX_PER_HOUR = Number(process.env.KANBAN_MAX_TASKS_PER_HOUR || 4);
export const SYSTEM_LABELS = new Set(["from-ui", "awaiting-triage"]);

export function repo(): string {
  return process.env.GITHUB_REPO || REPO_DEFAULT;
}

export function projectRef(): string {
  return process.env.GITHUB_PROJECT || PROJECT_DEFAULT;
}

export function boxId(): string {
  return process.env.SIMULAR_BOX_ID || BOX_ID_DEFAULT;
}

export function isSystemLabel(name: string): boolean {
  const n = name.toLowerCase();
  return (
    SYSTEM_LABELS.has(n) ||
    n.startsWith("status/") ||
    n.startsWith("bot/") ||
    n.startsWith("priority/")
  );
}

export function columnFromStatus(name: string | undefined | null): Column | null {
  const n = (name || "").toLowerCase().replace(/[_\s]+/g, "-");
  const aliases: Record<string, Column> = {
    backlog: "backlog",
    "to-do": "to-do",
    todo: "to-do",
    planning: "planning",
    "in-progress": "in-progress",
    inprogress: "in-progress",
    "in-review": "in-review",
    inreview: "in-review",
    review: "in-review",
    done: "done",
  };
  return aliases[n] || null;
}
