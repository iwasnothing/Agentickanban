import type { VercelRequest, VercelResponse } from "@vercel/node";
import { getIssue, sections } from "../../lib/github.js";
import { json, requireSession } from "../../lib/http.js";
import { acceptanceCriteria, parseLog } from "../../lib/parse.js";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "GET") return json(res, 405, { error: "method not allowed" });
  if (!requireSession(req, res)) return;
  const raw = String(req.query.id || "");
  const num = Number(String(raw).replace(/^AK-/i, ""));
  if (!Number.isInteger(num) || num <= 0) return json(res, 404, { error: "not found" });
  try {
    const { ticket, body } = await getIssue(num);
    const secs = sections(body);
    return json(res, 200, {
      id: ticket.id,
      key: ticket.key,
      meta: {
        id: ticket.id,
        title: ticket.title,
        status: ticket.status,
        assignee_bot: ticket.assignee_bot,
        github_issue: ticket.github_issue,
        github_project_item_id: ticket.project_item_id,
        priority: ticket.priority,
        labels: ticket.labels,
        created_at: ticket.created_at,
        updated_at: ticket.updated_at,
      },
      objective: secs["Objective"] || "",
      context: secs["Context"] || "",
      acceptance_criteria: acceptanceCriteria(body),
      final_review: secs["Final Review"] || "",
      log: parseLog(body),
      github_url: ticket.github_url,
    });
  } catch (e: any) {
    const status = e?.status === 404 ? 404 : 502;
    return json(res, status, { error: e instanceof Error ? e.message : "failed" });
  }
}
