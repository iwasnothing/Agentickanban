import type { VercelRequest, VercelResponse } from "@vercel/node";
import { cacheSet } from "../lib/cache.js";
import { bridgeAuthorized, json, readJson } from "../lib/http.js";

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "POST") return json(res, 405, { error: "method not allowed" });
  if (!bridgeAuthorized(req)) return json(res, 401, { error: "unauthorized" });
  let body: any;
  try {
    body = await readJson(req);
  } catch {
    return json(res, 400, { error: "invalid JSON" });
  }
  const services = body?.services;
  if (!services || typeof services !== "object") return json(res, 422, { error: "services object required" });
  const now = Date.now() / 1000;
  const packed = {
    superagent: services.superagent || null,
    openmausbot: services.openmausbot || null,
    received_at: now,
  };
  // Keep slightly longer than the 15s poll so a missed beat does not immediately go red.
  await cacheSet("heartbeat:services", packed, 120, ["health"]);
  return json(res, 200, { ok: true, received_at: now });
}
