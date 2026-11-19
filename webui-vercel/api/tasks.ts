import type { VercelRequest, VercelResponse } from "@vercel/node";
import { MAX_PER_HOUR } from "../lib/bots.js";
import { countFromUiSince, createTaskIssue, hasAwaitingTriage } from "../lib/github.js";
import { json, readJson, requireSession } from "../lib/http.js";
import { jobStats, upsertJob } from "../lib/jobs.js";
import { cleanList } from "../lib/parse.js";
import { randomBytes } from "node:crypto";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "POST") return json(res, 405, { error: "method not allowed" });
  if (!requireSession(req, res)) return;

  let body: any;
  try {
    body = await readJson(req);
  } catch {
    return json(res, 400, { error: "invalid JSON" });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json(res, 400, { error: "invalid JSON" });

  const title = String(body.title || "").replace(/\s+/g, " ").trim();
  const objective = String(body.objective || "").trim();
  const priority = String(body.priority || "medium").toLowerCase();
  const errs: string[] = [];
  if (!(title.length >= 4 && title.length <= 120)) errs.push("title must be 4–120 characters");
  if (!(objective.length >= 10 && objective.length <= 4000)) errs.push("description must be 10–4000 characters");
  if (!["high", "medium", "low"].includes(priority)) errs.push("priority must be high, medium or low");
  if (errs.length) return json(res, 422, { error: errs.join("; ") });

  const acceptance = cleanList(body.acceptance_criteria, 12, 300);
  const labels = cleanList(body.labels, 6, 24)
    .map((l) => l.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 24))
    .filter(Boolean);
  const runLoop = Boolean(body.run_loop);

  let used = 0;
  let inFlight = false;
  try {
    const hourAgo = Date.now() - 3600 * 1000;
    const [count, awaiting, stats] = await Promise.all([
      countFromUiSince(hourAgo),
      hasAwaitingTriage(),
      jobStats(0, false),
    ]);
    used = Math.max(count, stats.used_this_hour);
    inFlight = awaiting || stats.in_flight;
  } catch {
    const stats = await jobStats(0, false);
    used = stats.used_this_hour;
    inFlight = stats.in_flight;
  }
  if (inFlight) {
    return json(res, 429, {
      error: "The Superagent is already working on a request. Try again when it finishes.",
    });
  }
  if (used >= MAX_PER_HOUR) {
    return json(res, 429, { error: `Hourly limit reached (${MAX_PER_HOUR} tasks/hour). Try again later.` });
  }

  const marker = `webui-request:${randomBytes(5).toString("hex")}`;
  try {
    const created = await createTaskIssue({
      title,
      objective,
      acceptance,
      priority,
      labels,
      runLoop,
      marker,
    });
    await upsertJob({
      id: marker.replace("webui-request:", ""),
      title,
      status: "queued",
      stage: "queued",
      message: "Issue opened on GitHub; waiting for the box poller to hand it to Superagent",
      run_loop: runLoop,
      created_at: Date.now() / 1000,
      github_issue: created.number,
    });
    return json(res, 200, {
      ok: true,
      job_id: marker.replace("webui-request:", ""),
      github_issue: created.number,
      github_url: created.url,
    });
  } catch (e) {
    return json(res, 502, { error: e instanceof Error ? e.message : "failed to create issue" });
  }
}
