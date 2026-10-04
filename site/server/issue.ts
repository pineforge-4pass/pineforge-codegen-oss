// License issuance for a paid order: build the payload, sign it, store it
// (at most one license per order: licenses.order_id is UNIQUE) and, only when
// this call created the license, email the buyer and the sale notice.
import { addMonthsIso, nowIso } from "../lib/dates.ts";
import { generateLicenseId, signLicense, type LicenseMode, type LicensePayload, type LicenseScope } from "../lib/license.ts";
import { canonicalize } from "../lib/canonical-json.ts";
import { b64Encode } from "../lib/base64url.ts";
import { tierName } from "../lib/commerce.ts";
import { licenseByOrder, type LicenseRow, type OrderRow } from "./db.ts";
import { sendEmail } from "./email.ts";
import { signingKey } from "./keyring.ts";
import type { Env } from "./env.ts";

export interface IssueResult {
  license: LicenseRow;
  created: boolean;
}

export function buildPayload(order: OrderRow, mode: LicenseMode, id: string, issuedAt: string): LicensePayload {
  const scope = JSON.parse(order.scope_json) as LicenseScope;
  return {
    v: 1,
    id,
    product: "pineforge-codegen",
    licensee: { company: order.company, country: order.country },
    tier: order.tier,
    option: order.option_id,
    scope,
    term: { months: order.term_months, validFrom: issuedAt, validUntil: addMonthsIso(issuedAt, order.term_months) },
    issuedAt,
    mode,
    agreement: { version: order.agreement_version, sha256: order.agreement_sha256 },
    orderRef: order.id,
  };
}

/**
 * Issues the order's license unless it already has one. The insert only
 * happens while the order is still `paid` (one statement, so a full refund
 * that commits first leaves no license, and one that commits later revokes
 * it); null when nothing was stored. Throws when the signing key is missing
 * or invalid or D1 fails, so the webhook answers 500 and Stripe retries.
 */
export async function issueLicense(env: Env, order: OrderRow, mode: LicenseMode, siteUrl: string): Promise<IssueResult | null> {
  const existing = await licenseByOrder(env.DB, order.id);
  if (existing) return { license: existing, created: false };

  const key = signingKey(env);
  const issuedAt = nowIso();
  const payload = buildPayload(order, mode, generateLicenseId(), issuedAt);
  const signed = await signLicense(payload, key);
  const payloadJson = canonicalize(signed.license);

  const res = await env.DB.prepare(
    `INSERT INTO licenses (id, order_id, kid, mode, payload_json, signature, status, issued_at, valid_from, valid_until)
     SELECT ?1, ?2, ?3, ?4, ?5, ?6, 'active', ?7, ?8, ?9
     WHERE EXISTS (SELECT 1 FROM orders WHERE id = ?2 AND status = 'paid')
     ON CONFLICT (order_id) DO NOTHING`,
  )
    .bind(
      payload.id,
      order.id,
      signed.signature.kid,
      mode,
      payloadJson,
      signed.signature.value,
      issuedAt,
      payload.term.validFrom,
      payload.term.validUntil,
    )
    .run();

  const stored = await licenseByOrder(env.DB, order.id);
  if (!stored) return null; // the order stopped being paid (refunded) before the insert
  const created = (res.meta?.changes ?? 0) > 0 && stored.id === payload.id;
  if (created) await sendIssueEmails(env, order, stored, siteUrl);
  return { license: stored, created };
}

async function sendIssueEmails(env: Env, order: OrderRow, row: LicenseRow, siteUrl: string): Promise<void> {
  const prefix = row.mode === "test" ? "[TEST] " : "";
  const signedJson = JSON.stringify(
    { license: JSON.parse(row.payload_json), signature: { alg: "Ed25519", kid: row.kid, value: row.signature } },
    null,
    2,
  );
  const certificateUrl = `${siteUrl}/certificate/${row.id}`;
  const verifyUrl = `${siteUrl}/${order.locale}/verify/?id=${row.id}`;
  const keysUrl = `${siteUrl}/.well-known/pineforge-license-keys.json`;
  const testNote =
    row.mode === "test"
      ? ["", "TEST LICENSE: issued from a Stripe test-mode payment; it is not a commercial license.", ""]
      : [""];

  await sendEmail(env, {
    to: [order.buyer_email],
    subject: `${prefix}Your PineForge Codegen commercial license ${row.id}`,
    text: [
      `Hello ${order.buyer_name},`,
      "",
      `Thank you for your order. The commercial license for ${order.company} has been issued.`,
      ...testNote,
      `License id:   ${row.id}`,
      `Product:      ${order.product_name}`,
      `Valid:        ${row.valid_from} to ${row.valid_until}`,
      `Order:        ${order.id}`,
      "",
      `Certificate:  ${certificateUrl}`,
      `Verify:       ${verifyUrl}`,
      "",
      `The signed license file ${row.id}.json is attached. Its Ed25519 signature can be`,
      `checked offline against the public keys at ${keysUrl}.`,
      "Stripe sends the invoice and receipt separately.",
      "",
      `Questions: ${env.LICENSE_NOTIFY_TO ?? "enterprise@pineforge.dev"}`,
    ].join("\n"),
    attachments: [{ filename: `${row.id}.json`, content: b64Encode(new TextEncoder().encode(signedJson + "\n")) }],
    kind: "license",
    relatedId: row.id,
  });

  const notifyTo = (env.LICENSE_NOTIFY_TO ?? "").trim();
  await sendEmail(env, {
    to: notifyTo ? [notifyTo] : [],
    subject: `${prefix}License sale: ${row.id} — ${order.company} (${order.tier}/${order.option_id})`,
    text: [
      `License:     ${row.id} (${row.mode})`,
      `Order:       ${order.id}`,
      `Licensee:    ${order.company}, ${order.country}`,
      `Buyer:       ${order.buyer_name} <${order.buyer_email}>`,
      `Reference:   ${order.reference ?? ""}`,
      `Plan:        ${tierName(order.tier)} / ${order.option_id} (${order.scope_summary})`,
      `Amount:      ${order.amount_subtotal} ${order.currency} (minor units, before tax)`,
      `Term:        ${row.valid_from} to ${row.valid_until}`,
      `Agreement:   ${order.agreement_version} (sha256 ${order.agreement_sha256})`,
      `Stripe:      session ${order.stripe_session_id ?? ""}, payment ${order.stripe_payment_intent ?? ""}, invoice ${order.stripe_invoice ?? ""}`,
      `Certificate: ${certificateUrl}`,
    ].join("\n"),
    kind: "sale",
    relatedId: row.id,
  });
}
