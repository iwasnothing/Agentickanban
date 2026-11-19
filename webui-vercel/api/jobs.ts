import type { VercelRequest, VercelResponse } from "@vercel/node";
import { countFromUiSince, hasAwaitingTriage } from "../lib/github.js";
import { bridgeAuthorized, json, readJson, requireSession } from "../lib/http.js";
import { jobStats, upsertJob } from "../lib/jobs.js";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method === "GET") {
    if (!requireSession(req, res)) return;
    try {
      const hourAgo = Date.now() - 3600 * 1000;
      const [count, awaiting] = await Promise.all([countFromUiSince(hourAgo), hasAwaitingTriage()]);
      return json(res, 200, await jobStats(count, awaiting));
    } catch (e) {
      return json(res, 200, await jobStats(0, false));
    }
  }
  if (req.method === "POST") {
    if (!bridgeAuthorized(req)) return json(res, 401, { error: "unauthorized" });
    let body: any;
    try {
      body = await readJson(req);
    } catch {
      return json(res, 400, { error: "invalid JSON" });
    }
    if (!body?.id || typeof body.id !== "string") return json(res, 422, { error: "id required" });
    const job = await upsertJob(body);
    return json(res, 200, { ok: true, job });
  }
  return json(res, 405, { error: "method not allowed" });
}
