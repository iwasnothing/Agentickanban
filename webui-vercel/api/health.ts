import type { VercelRequest, VercelResponse } from "@vercel/node";
import { cacheBackendName, cacheGet } from "../lib/cache.js";
import { json, requireSession } from "../lib/http.js";
import { checkBox, type BoxHealth } from "../lib/simular.js";

type Svc = {
  name: string;
  sub: string;
  status: "green" | "amber" | "red";
  latency_ms: number | null;
  detail: string;
  checked_at: number;
  model?: string;
};

function stale(svc: Svc | undefined, name: string, sub: string): Svc {
  if (!svc) {
    return {
      name,
      sub,
      status: "red",
      latency_ms: null,
      detail: "No heartbeat from the box yet (the kanban-bridge poller is not posting, or BRIDGE_SECRET / Vercel URL is wrong).",
      checked_at: Date.now() / 1000,
    };
  }
  const age = Date.now() / 1000 - (svc.checked_at || 0);
  if (age > 90) return { ...svc, status: "red", detail: `${svc.detail} · heartbeat ${Math.round(age)}s stale` };
  if (age > 45 && svc.status === "green") return { ...svc, status: "amber", detail: `${svc.detail} · heartbeat ${Math.round(age)}s old` };
  return svc;
}

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "GET") return json(res, 405, { error: "method not allowed" });
  if (!requireSession(req, res)) return;
  const hb = (await cacheGet<{ superagent?: Svc; openmausbot?: Svc; received_at?: number }>("heartbeat:services")) || {};
  const superagent = stale(hb.superagent, "Superagent", "DSH manager · box heartbeat");
  const openmausbot = stale(hb.openmausbot, "OpenMausBot", "worker harness · box heartbeat");
  let box: BoxHealth;
  try {
    box = await checkBox();
  } catch (e) {
    box = {
      name: "Simular VM",
      sub: "box MCP · mcp.box.simular.ai",
      status: "red",
      latency_ms: null,
      detail: e instanceof Error ? e.message.slice(0, 200) : "box check failed",
      checked_at: Date.now() / 1000,
    };
  }
  return json(res, 200, {
    services: [superagent, openmausbot, box],
    now: Date.now() / 1000,
    cache: cacheBackendName(),
  });
}
