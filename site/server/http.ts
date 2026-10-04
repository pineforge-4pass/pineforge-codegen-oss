// Response and request helpers shared by the Functions.

const SECURITY_HEADERS: Record<string, string> = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...SECURITY_HEADERS, "Content-Type": "application/json; charset=utf-8" },
  });
}

export function apiError(status: number, error: string, extra?: Record<string, unknown>): Response {
  return json(status, { error, ...extra });
}

export function html(status: number, body: string, extraHeaders: Record<string, string> = {}): Response {
  return new Response(body, {
    status,
    headers: { ...SECURITY_HEADERS, "Content-Type": "text/html; charset=utf-8", ...extraHeaders },
  });
}

/** The client address Cloudflare reports; "unknown" when absent (local tools). */
export function clientIp(request: Request): string {
  return request.headers.get("CF-Connecting-IP")?.trim() || "unknown";
}

export class BodyError extends Error {}

/**
 * Reads a JSON request body of at most `maxBytes`. Throws BodyError for an
 * oversized body or invalid JSON.
 */
export async function readJson(request: Request, maxBytes = 64 * 1024): Promise<unknown> {
  const declared = Number(request.headers.get("Content-Length") ?? "0");
  if (declared > maxBytes) throw new BodyError("body too large");
  const text = await request.text();
  if (new TextEncoder().encode(text).length > maxBytes) throw new BodyError("body too large");
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new BodyError("invalid json");
  }
}

/** `<prefix>_<24 lowercase hex chars>` (96 random bits). */
export function randomId(prefix: string): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return `${prefix}_${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Fills `{name}` placeholders of a catalog string (plain text in, plain text out). */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in values ? String(values[k]) : m));
}
