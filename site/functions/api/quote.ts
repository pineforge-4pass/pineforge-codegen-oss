// POST /api/quote
//
// "Talk to us" requests: validated, rate-limited per client IP, stored in D1
// FIRST, then emailed to LICENSE_NOTIFY_TO with reply-to the requester. No
// auto-reply. A filled honeypot gets a 200 and nothing is stored or sent.
// When TURNSTILE_SECRET is set, a valid Turnstile token (`turnstileToken`)
// is required as well.
import { HONEYPOT_FIELD, validateQuote } from "../../lib/validate.ts";
import { nowIso } from "../../lib/dates.ts";
import { safeLocale, type Env } from "../../server/env.ts";
import { BodyError, apiError, clientIp, json, randomId, readJson } from "../../server/http.ts";
import { QUOTE_LIMIT, allowRequest } from "../../server/rate-limit.ts";
import { sendEmail } from "../../server/email.ts";

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  let body: unknown;
  try {
    body = await readJson(request, 32 * 1024);
  } catch (e) {
    if (!(e instanceof BodyError)) console.error("[quote] reading the body failed:", e);
    return apiError(400, "invalid_json");
  }
  const fields = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  const trap = fields[HONEYPOT_FIELD];
  if (typeof trap === "string" && trap.trim() !== "") return json(200, { ok: true });

  const checked = validateQuote(body);
  if (!checked.ok) return apiError(400, "invalid_fields", { fields: checked.errors });
  const q = checked.value;
  const ip = clientIp(request);

  try {
    if (!(await allowRequest(env.DB, QUOTE_LIMIT, ip))) return apiError(429, "rate_limited");
  } catch (e) {
    console.error("[quote] rate limit failed:", e);
    return apiError(500, "server_error");
  }

  const secret = (env.TURNSTILE_SECRET ?? "").trim();
  if (secret) {
    const token = typeof fields.turnstileToken === "string" ? fields.turnstileToken : "";
    if (!token || !(await turnstileOk(secret, token, ip))) return apiError(400, "captcha_failed");
  }

  const id = randomId("quo");
  const locale = safeLocale(q.locale);
  try {
    await env.DB.prepare(
      `INSERT INTO quotes (id, name, email, company, use_case, seats, aum, deployment, message, locale, created_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)`,
    )
      .bind(id, q.name, q.email, q.company, q.useCase, q.seats || null, q.aum || null, q.deployment || null, q.message, locale, nowIso())
      .run();
  } catch (e) {
    console.error("[quote] insert failed:", e);
    return apiError(500, "server_error");
  }

  const to = (env.LICENSE_NOTIFY_TO ?? "").trim();
  await sendEmail(env, {
    to: to ? [to] : [],
    replyTo: q.email,
    subject: `Quote request: ${q.company} (${q.useCase})`,
    text: [
      `Quote:       ${id}`,
      `Name:        ${q.name}`,
      `Email:       ${q.email}`,
      `Company:     ${q.company}`,
      `Use case:    ${q.useCase}`,
      `Seats:       ${q.seats}`,
      `AUM:         ${q.aum}`,
      `Deployment:  ${q.deployment}`,
      `Locale:      ${locale}`,
      "",
      "Message:",
      q.message,
    ].join("\n"),
    kind: "quote",
    relatedId: id,
  });
  return json(200, { ok: true });
};

/** Cloudflare Turnstile server-side check; fails closed. */
async function turnstileOk(secret: string, token: string, ip: string): Promise<boolean> {
  try {
    const form = new URLSearchParams({ secret, response: token });
    if (ip !== "unknown") form.set("remoteip", ip);
    const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(10_000),
    });
    const data = (await res.json()) as { success?: unknown };
    return data.success === true;
  } catch (e) {
    console.error("[quote] turnstile check failed:", e);
    return false;
  }
}
