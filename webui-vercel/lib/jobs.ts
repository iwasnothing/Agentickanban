import { cacheGet, cacheSet } from "./cache.js";
import { MAX_PER_HOUR } from "./bots.js";

export type Job = {
  id: string;
  title: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  message: string;
  run_loop?: boolean;
  created_at: number;
  updated_at: number;
  ticket_id?: string | null;
  github_issue?: number | null;
  events?: { t: number; stage: string; msg: string }[];
};

const KEY = "jobs:list";

export async function loadJobs(): Promise<Job[]> {
  return (await cacheGet<Job[]>(KEY)) || [];
}

export async function saveJobs(jobs: Job[]): Promise<void> {
  await cacheSet(KEY, jobs.slice(-200), 7 * 24 * 3600, ["jobs"]);
}

export async function upsertJob(patch: Partial<Job> & { id: string }): Promise<Job> {
  const jobs = await loadJobs();
  const now = Date.now() / 1000;
  const idx = jobs.findIndex((j) => j.id === patch.id);
  if (idx < 0) {
    const job: Job = {
      id: patch.id,
      title: patch.title || "task",
      status: patch.status || "queued",
      stage: patch.stage || "queued",
      message: patch.message || "Queued",
      run_loop: patch.run_loop,
      created_at: patch.created_at || now,
      updated_at: now,
      ticket_id: patch.ticket_id ?? null,
      github_issue: patch.github_issue ?? null,
      events: [{ t: now, stage: patch.stage || "queued", msg: patch.message || "Queued" }],
    };
    jobs.push(job);
    await saveJobs(jobs);
    return job;
  }
  const prev = jobs[idx];
  const next: Job = { ...prev, ...patch, updated_at: now, events: prev.events || [] };
  if (patch.stage || patch.message) {
    next.events = [...(next.events || []), { t: now, stage: patch.stage || prev.stage, msg: patch.message || "" }];
  }
  jobs[idx] = next;
  await saveJobs(jobs);
  return next;
}

export async function jobStats(fromUiLastHour: number, awaiting: boolean) {
  const jobs = await loadJobs();
  const hourJobs = jobs.filter((j) => Date.now() / 1000 - j.created_at < 3600);
  const used = Math.max(hourJobs.length, fromUiLastHour);
  const inFlight =
    awaiting || jobs.some((j) => j.status === "queued" || j.status === "running");
  return {
    jobs: [...jobs].reverse().slice(0, 20),
    limit_per_hour: MAX_PER_HOUR,
    used_this_hour: used,
    in_flight: inFlight,
  };
}
