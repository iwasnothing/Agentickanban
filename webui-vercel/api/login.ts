import type { VercelRequest, VercelResponse } from "@vercel/node";
import { cacheGet, cacheSet } from "../lib/cache.js";
import { json, readJson, timingSafeString } from "../lib/http.js";
import { clientIp, setSessionCookie, signSession } from "../lib/session.js";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "POST") return json(res, 405, { error: "method not allowed" });
  const invite = process.env.KANBAN_INVITE_CODE || "";
  if (!invite) return json(res, 500, { error: "server is missing KANBAN_INVITE_CODE" });

  const ip = clientIp(req);
  const key = `login:${ip}`;
  const hist = ((await cacheGet<number[]>(key)) || []).filter((t) => Date.now() / 1000 - t < 600);
  if (hist.length >= 10) return json(res, 429, { error: "too many attempts, try again later" });
  hist.push(Date.now() / 1000);
  await cacheSet(key, hist, 600, ["login"]);

  let body: any;
  try {
    body = await readJson(req);
  } catch {
    return json(res, 400, { error: "invalid JSON" });
  }
  const code = String(body?.code || "").trim();
  if (!code || !timingSafeString(code, invite)) {
    return json(res, 401, { error: "invalid invite code" });
  }
  const token = signSession({ u: "invitee" });
  setSessionCookie(req, res, token);
  return json(res, 200, { ok: true });
}
