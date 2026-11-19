import { timingSafeEqual } from "node:crypto";
import type { VercelRequest, VercelResponse } from "@vercel/node";
import { isAuthed } from "./session.js";

export function applySecurity(res: VercelResponse): void {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Cache-Control", "no-store");
}

export function json(res: VercelResponse, status: number, body: unknown): void {
  applySecurity(res);
  res.status(status).json(body);
}

export function requireSession(req: VercelRequest, res: VercelResponse): boolean {
  if (isAuthed(req)) return true;
  json(res, 401, { error: "unauthorized" });
  return false;
}

export function timingSafeString(a: string, b: string): boolean {
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ba.length !== bb.length) {
    timingSafeEqual(ba, ba);
    return false;
  }
  return timingSafeEqual(ba, bb);
}

export function bridgeAuthorized(req: VercelRequest): boolean {
  const expected = process.env.BRIDGE_SECRET || "";
  if (!expected) return false;
  const header = String(req.headers.authorization || "");
  const bearer = header.toLowerCase().startsWith("bearer ") ? header.slice(7).trim() : "";
  return Boolean(bearer && timingSafeString(bearer, expected));
}

export async function readJson(req: VercelRequest): Promise<unknown> {
  if (req.body !== undefined && req.body !== null && req.body !== "") {
    if (typeof req.body === "string") {
      try {
        return JSON.parse(req.body);
      } catch {
        throw Object.assign(new Error("invalid JSON"), { status: 400 });
      }
    }
    return req.body;
  }
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    throw Object.assign(new Error("invalid JSON"), { status: 400 });
  }
}
