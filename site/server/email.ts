// Resend sender. Every attempt is logged in email_log; nothing here throws,
// so a failed email never fails the request that triggered it.
import { testOverride, type Env } from "./env.ts";
import { nowIso } from "../lib/dates.ts";

/**
 * "alert-*" kinds are alerts sent at most once per related id
 * (`sendAlertOnce`): alert-issue per order, alert-dispute and
 * alert-dispute-lost per Stripe dispute.
 */
export type EmailKind = "license" | "sale" | "quote" | "alert" | "alert-issue" | "alert-dispute" | "alert-dispute-lost";

export interface OutgoingEmail {
  to: string[];
  subject: string;
  text: string;
  replyTo?: string;
  attachments?: { filename: string; content: string }[]; // content: standard base64
  kind: EmailKind;
  relatedId: string | null;
}

export interface SendResult {
  status: "sent" | "failed" | "skipped";
  providerId: string | null;
  error: string | null;
}

async function send(env: Env, msg: OutgoingEmail): Promise<SendResult> {
  const apiKey = (env.RESEND_API_KEY ?? "").trim();
  const from = (env.RESEND_FROM ?? "").trim();
  if (!apiKey || !from) return { status: "skipped", providerId: null, error: "RESEND_API_KEY or RESEND_FROM not set" };
  if (msg.to.length === 0 || msg.to.some((t) => !t)) return { status: "skipped", providerId: null, error: "no recipient" };
  const base = (testOverride(env, "RESEND_API_BASE") || "https://api.resend.com").replace(/\/+$/, "");
  const body: Record<string, unknown> = { from, to: msg.to, subject: msg.subject, text: msg.text };
  if (msg.replyTo) body.reply_to = msg.replyTo;
  if (msg.attachments?.length) body.attachments = msg.attachments;
  try {
    const res = await fetch(`${base}/emails`, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    const text = await res.text();
    if (!res.ok) return { status: "failed", providerId: null, error: `HTTP ${res.status}: ${text.slice(0, 500)}` };
    let id: string | null = null;
    try {
      const data = JSON.parse(text) as { id?: unknown };
      id = typeof data.id === "string" ? data.id : null;
    } catch {
      // A 2xx without a JSON id still counts as accepted.
    }
    return { status: "sent", providerId: id, error: null };
  } catch (e) {
    return { status: "failed", providerId: null, error: String(e instanceof Error ? e.message : e).slice(0, 500) };
  }
}

/** Sends through Resend and records the attempt; never throws. */
export async function sendEmail(env: Env, msg: OutgoingEmail): Promise<SendResult> {
  const result = await send(env, msg);
  if (result.status !== "sent") console.error(`[email] ${msg.kind} ${result.status}: ${result.error}`);
  await recordEmail(env, msg, result);
  return result;
}

/** Records an email attempt (or a decision not to send one) in email_log; never throws. */
export async function recordEmail(env: Env, msg: OutgoingEmail, result: SendResult): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO email_log (to_addr, subject, kind, related_id, provider_id, status, error, created_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)`,
    )
      .bind(msg.to.join(", "), msg.subject, msg.kind, msg.relatedId, result.providerId, result.status, result.error, nowIso())
      .run();
  } catch (e) {
    console.error("[email] email_log insert failed:", e);
  }
}

/** An operator alert to LICENSE_NOTIFY_TO; the subject starts with "ALERT:". */
export async function sendAlert(
  env: Env,
  subject: string,
  lines: string[],
  relatedId: string | null,
  kind: EmailKind = "alert",
): Promise<void> {
  const to = (env.LICENSE_NOTIFY_TO ?? "").trim();
  await sendEmail(env, {
    to: to ? [to] : [],
    subject: `ALERT: ${subject}`,
    text: lines.join("\n"),
    kind,
    relatedId,
  });
}

/**
 * An alert sent once per (kind, relatedId): skipped when email_log already
 * holds a row of that kind for that id that is not a failed attempt (a
 * failed one is retried by the next call). Never throws; when the lookup
 * itself fails the alert is sent anyway.
 */
export async function sendAlertOnce(
  env: Env,
  kind: EmailKind,
  relatedId: string,
  subject: string,
  lines: string[],
): Promise<void> {
  try {
    const prior = await env.DB.prepare(
      "SELECT id FROM email_log WHERE kind = ?1 AND related_id = ?2 AND status <> 'failed' LIMIT 1",
    )
      .bind(kind, relatedId)
      .first();
    if (prior) return;
  } catch (e) {
    console.error(`[email] ${kind} dedupe lookup failed:`, e);
  }
  await sendAlert(env, subject, lines, relatedId, kind);
}
