import type { VercelRequest, VercelResponse } from "@vercel/node";
import { BOTS, COLUMNS, projectRef, repo } from "../lib/bots.js";
import { listBoardTickets } from "../lib/github.js";
import { json, requireSession } from "../lib/http.js";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "GET") return json(res, 405, { error: "method not allowed" });
  if (!requireSession(req, res)) return;
  try {
    const tickets = await listBoardTickets();
    const r = repo();
    const pref = projectRef();
    const [owner, num] = pref.includes("/") ? pref.split("/") : [r.split("/")[0], pref];
    return json(res, 200, {
      columns: COLUMNS,
      bots: BOTS,
      tickets,
      generated_at: Date.now() / 1000,
      repo: r,
      project_url: `https://github.com/users/${owner}/projects/${num}`,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "board failed";
    return json(res, 502, { error: msg });
  }
}
