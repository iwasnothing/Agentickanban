/**
 * Heartbeats, job status and rate-limit counters live in Vercel Runtime Cache
 * when running on Vercel. Local `vercel dev` (and any getCache failure) falls
 * back to in-process memory, which is fragile: it is lost on scale-to-zero,
 * cold starts, and is not shared across instances. Do not treat the memory
 * fallback as durable storage.
 */

type CacheLike = {
  get: (key: string) => Promise<unknown>;
  set: (key: string, value: unknown, opts?: { ttl?: number; tags?: string[] }) => Promise<void>;
  delete?: (key: string) => Promise<void>;
};

const memory = new Map<string, { value: unknown; exp: number }>();

const memoryCache: CacheLike = {
  async get(key) {
    const hit = memory.get(key);
    if (!hit) return undefined;
    if (hit.exp && hit.exp < Date.now()) {
      memory.delete(key);
      return undefined;
    }
    return hit.value;
  },
  async set(key, value, opts) {
    const ttl = opts?.ttl ?? 3600;
    memory.set(key, { value, exp: Date.now() + ttl * 1000 });
  },
  async delete(key) {
    memory.delete(key);
  },
};

let runtime: CacheLike | null | undefined;

async function backend(): Promise<CacheLike> {
  if (runtime !== undefined) return runtime ?? memoryCache;
  try {
    const mod = await import("@vercel/functions");
    if (typeof mod.getCache === "function") {
      runtime = mod.getCache({ namespace: "kanban" }) as CacheLike;
      return runtime;
    }
  } catch {
    // not on Vercel, or the Runtime Cache API is unavailable
  }
  runtime = null;
  return memoryCache;
}

export function cacheBackendName(): "runtime-cache" | "memory-fallback" {
  return runtime ? "runtime-cache" : "memory-fallback";
}

export async function cacheGet<T>(key: string): Promise<T | undefined> {
  const v = await (await backend()).get(key);
  return v as T | undefined;
}

export async function cacheSet(key: string, value: unknown, ttlSeconds: number, tags?: string[]): Promise<void> {
  await (await backend()).set(key, value, { ttl: ttlSeconds, tags });
}
