import { createHmac, timingSafeEqual } from "node:crypto";
import type { VercelRequest, VercelResponse } from "@vercel/node";
import { COOKIE, SESSION_MAX_AGE } from "./bots.js";

function secret(): string {
  const s = process.env.SESSION_SECRET || "";
  if (!s) throw new Error("SESSION_SECRET is not set");
  return s;
}

function b64url(buf: Buffer | string): string {
  const b = typeof buf === "string" ? Buffer.from(buf) : buf;
  return b.toString("base64url");
}

export function signSession(payload: object, now = Math.floor(Date.now() / 1000)): string {
  const body = b64url(JSON.stringify({ ...payload, iat: now }));
  const sig = createHmac("sha256", secret()).update(body).digest();
  return `${body}.${b64url(sig)}`;
}

export function verifySession(token: string | undefined): { ok: true; iat: number } | { ok: false } {
  if (!token || !token.includes(".")) return { ok: false };
  const [body, sig] = token.split(".");
  if (!body || !sig) return { ok: false };
  const expected = b64url(createHmac("sha256", secret()).update(body).digest());
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return { ok: false };
  try {
    const data = JSON.parse(Buffer.from(body, "base64url").toString("utf8")) as { iat?: number };
    const iat = Number(data.iat || 0);
    if (!iat || Math.floor(Date.now() / 1000) - iat > SESSION_MAX_AGE) return { ok: false };
    return { ok: true, iat };
  } catch {
    return { ok: false };
  }
}

export function readCookie(req: VercelRequest, name = COOKIE): string | undefined {
  const fromMap = req.cookies?.[name];
  if (fromMap) return fromMap;
  const header = req.headers.cookie;
  if (!header) return undefined;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return undefined;
}

export function isAuthed(req: VercelRequest): boolean {
  return verifySession(readCookie(req)).ok;
}

export function setSessionCookie(req: VercelRequest, res: VercelResponse, token: string): void {
  const proto = String(req.headers["x-forwarded-proto"] || "").split(",")[0] || "http";
  const secure = proto === "https";
  const parts = [
    `${COOKIE}=${token}`,
    "HttpOnly",
    "Path=/",
    "SameSite=Lax",
    `Max-Age=${SESSION_MAX_AGE}`,
  ];
  if (secure) parts.push("Secure");
  res.setHeader("Set-Cookie", parts.join("; "));
}

export function clearSessionCookie(res: VercelResponse): void {
  res.setHeader("Set-Cookie", `${COOKIE}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0`);
}

export function clientIp(req: VercelRequest): string {
  const cf = req.headers["cf-connecting-ip"] || req.headers["x-real-ip"];
  if (typeof cf === "string" && cf) return cf.split(",")[0].trim();
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string" && fwd) return fwd.split(",")[0].trim();
  return req.socket?.remoteAddress || "?";
}
