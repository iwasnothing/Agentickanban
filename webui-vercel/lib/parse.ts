export type Criterion = { done: boolean; text: string };
export type LogEntry = { heading: string; lines: string[] };

export function sections(body: string): Record<string, string> {
  const text = body || "";
  const matches = [...text.matchAll(/^## (.+?)\s*$/gm)];
  if (!matches.length) return { Objective: text.trim() };
  const out: Record<string, string> = {};
  for (let i = 0; i < matches.length; i++) {
    const name = matches[i][1].trim();
    const start = (matches[i].index || 0) + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index! : text.length;
    out[name] = text.slice(start, end).trim();
  }
  return out;
}

export function acceptanceCriteria(body: string): Criterion[] {
  const block = sections(body)["Acceptance Criteria"] || "";
  const out: Criterion[] = [];
  for (const line of block.split("\n")) {
    const m = line.match(/^\s*-\s*\[([ xX])\]\s*(.+)$/);
    if (m) out.push({ done: m[1].toLowerCase() === "x", text: m[2].trim() });
  }
  return out;
}

export function parseLog(body: string): LogEntry[] {
  const log = sections(body)["Execution Log"] || "";
  const entries: LogEntry[] = [];
  let cur: LogEntry | null = null;
  for (const line of log.split("\n")) {
    const m = line.match(/^###?\s+(.+?)\s*$/);
    if (m) {
      cur = { heading: m[1], lines: [] };
      entries.push(cur);
    } else if (line.trim() && cur) {
      cur.lines.push(line.trim().replace(/^-\s+/, ""));
    }
  }
  return entries;
}

export function parseMarker(body: string): { marker: string | null; runLoop: boolean } {
  const comment = body.match(/<!--\s*kanban:\s*marker=(\S+)\s+run_loop=(true|false)\s*-->/i);
  if (comment) return { marker: comment[1], runLoop: comment[2].toLowerCase() === "true" };
  const m = body.match(/webui-request:[A-Za-z0-9_-]+/);
  const run = /run_loop:\s*true/i.test(body) || /run_loop=true/i.test(body);
  return { marker: m ? m[0] : null, runLoop: run };
}

export function cleanList(v: unknown, n: number, ln: number): string[] {
  const arr = typeof v === "string" ? v.split(/\r?\n/) : Array.isArray(v) ? v : [];
  const out: string[] = [];
  for (const raw of arr) {
    const x = String(raw)
      .replace(/^\s*[-*]\s*(\[[ xX]\]\s*)?/, "")
      .trim()
      .slice(0, ln);
    if (x) out.push(x);
    if (out.length >= n) break;
  }
  return out;
}
