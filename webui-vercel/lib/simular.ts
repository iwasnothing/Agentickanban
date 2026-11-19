import { boxId, BOX_MCP } from "./bots.js";
import { cacheGet, cacheSet } from "./cache.js";

export type BoxHealth = {
  name: string;
  sub: string;
  status: "green" | "amber" | "red";
  latency_ms: number | null;
  detail: string;
  state?: string;
  box_id?: string;
  image?: string;
  checked_at: number;
};

export async function checkBox(): Promise<BoxHealth> {
  const cached = await cacheGet<BoxHealth>("health:box");
  if (cached && Date.now() / 1000 - cached.checked_at < 60) return cached;

  const token = process.env.SIMULAR_BOX_TOKEN || "";
  const base: Omit<BoxHealth, "status" | "latency_ms" | "detail" | "state"> = {
    name: "Simular VM",
    sub: "box MCP · mcp.box.simular.ai",
    checked_at: Date.now() / 1000,
  };
  if (!token) {
    const v: BoxHealth = { ...base, status: "red", latency_ms: null, detail: "SIMULAR_BOX_TOKEN is not set", state: "error" };
    return v;
  }

  const t0 = Date.now();
  try {
    const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
    const { StreamableHTTPClientTransport } = await import(
      "@modelcontextprotocol/sdk/client/streamableHttp.js"
    );
    const transport = new StreamableHTTPClientTransport(new URL(BOX_MCP), {
      requestInit: { headers: { Authorization: `Bearer ${token}` } },
    });
    const client = new Client({ name: "agentic-kanban-web", version: "1.0.0" });
    await client.connect(transport);
    try {
      // Read-only: never call box_create / box_ensure / box_wake on a health poll.
      const res = await client.callTool({ name: "box_list", arguments: {} });
      const ms = Date.now() - t0;
      let data: any = res.structuredContent;
      if (!data) {
        const text = (res.content as any[])?.find((c) => c?.type === "text")?.text;
        data = text ? JSON.parse(text) : {};
      }
      const boxes = data?.boxes || [];
      const wanted = boxId();
      const b = boxes.find((x: any) => x.box_id === wanted) || boxes[0];
      let v: BoxHealth;
      if (!b) {
        v = { ...base, status: "red", latency_ms: ms, detail: "no box found on the account", state: "none" };
      } else {
        const state = b.state || "unknown";
        const st = state === "running" ? "green" : ["hibernated", "starting", "waking", "queued", "saving"].includes(state) ? "amber" : "red";
        const extra = state === "hibernated" ? " (wakes on demand when a bot needs it)" : "";
        v = {
          ...base,
          status: st,
          latency_ms: ms,
          state,
          box_id: b.box_id,
          image: b.image,
          detail: `${b.name} · ${b.box_id} · ${state}${extra} · ${b.image || ""}`.trim(),
          checked_at: Date.now() / 1000,
        };
      }
      await cacheSet("health:box", v, 60, ["health"]);
      return v;
    } finally {
      await client.close().catch(() => undefined);
    }
  } catch (e) {
    const ms = Date.now() - t0;
    const err = e instanceof Error ? `${e.name}: ${e.message}` : "MCP check failed";
    return {
      ...base,
      status: "red",
      latency_ms: ms,
      state: "error",
      detail: `MCP check failed: ${err}`.slice(0, 200),
      checked_at: Date.now() / 1000,
    };
  }
}
