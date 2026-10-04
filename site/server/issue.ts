// License issuance for a paid order: build the payload, sign it, store it
// (at most one license per order: licenses.order_id is UNIQUE) and, only when
// this call created the license, email the buyer and the sale notice.
import { addMonthsIso, nowIso } from "../lib/dates.ts";
import {
  generateLicenseId,
  signLicense,
  verifyLicenseSignature,
  type LicenseMode,
  type LicensePayload,
  type LicenseScope,
} from "../lib/license.ts";
import { canonicalize } from "../lib/canonical-json.ts";
import { b64Encode } from "../lib/base64url.ts";
import { tierName } from "../lib/commerce.ts";
import { licenseById, licenseByOrder, type LicenseRow, type OrderRow } from "./db.ts";
import { recordEmail, sendAlert, sendAlertOnce, sendEmail, type OutgoingEmail } from "./email.ts";
import { isProductionKid, signingKey, trustedKeyring } from "./keyring.ts";
import { deploymentMode, type Env } from "./env.ts";
import { isAllowlisted } from "../lib/email-allowlist.ts";

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
 * or invalid, is not in the trusted keyring, is a production key asked to
 * sign a test license, or D1 fails: the operator gets one alert per order
 * and the webhook answers 500, so Stripe retries.
 */
export async function issueLicense(env: Env, order: OrderRow, mode: LicenseMode, siteUrl: string): Promise<IssueResult | null> {
  let outcome: { license: LicenseRow | null; created: boolean };
  try {
    outcome = await storeLicense(env, order, mode);
  } catch (e) {
    await alertIssueFailure(env, order, mode, e);
    throw e;
  }
  const { license: stored, created } = outcome;
  if (!stored) return null; // the order stopped being paid (refunded) before the insert
  if (!created) {
    // A redelivery after the buyer's email failed sends it now (no-op once sent).
    if (stored.status === "active") await deliverBuyerEmail(env, order, stored, siteUrl);
    return { license: stored, created: false };
  }
  // The sale notice goes out once, from the delivery that created the
  // license; the buyer's email is retried until Resend accepts it.
  let buyerError: unknown = null;
  try {
    await deliverBuyerEmail(env, order, stored, siteUrl);
  } catch (e) {
    buyerError = e;
  }
  await sendSaleNotice(env, order, stored, siteUrl);
  if (buyerError) throw buyerError;
  return { license: stored, created };
}

/** The order's license, signing and storing it first when it has none; license null when the order is no longer paid. */
async function storeLicense(env: Env, order: OrderRow, mode: LicenseMode): Promise<{ license: LicenseRow | null; created: boolean }> {
  const existing = await licenseByOrder(env.DB, order.id);
  if (existing) return { license: existing, created: false };

  const key = signingKey(env);
  // A production key never signs a test license: such a license would verify
  // as genuine against the published keyring.
  if (mode === "test" && isProductionKid(key.kid)) {
    throw new Error(`refusing to sign a test license with production key ${key.kid}; set a test LICENSE_SIGNING_KEY`);
  }
  const issuedAt = nowIso();
  const payload = buildPayload(order, mode, generateLicenseId(), issuedAt);
  const signed = await signLicense(payload, key);
  // Store only a license that verifies where it will be checked: a signing key
  // missing from the trusted keyring (a preview key, a mistyped secret) would
  // issue licenses that verify as unknown_key. Throwing answers 500, so Stripe
  // retries the event once the configuration is fixed.
  const selfCheck = await verifyLicenseSignature(signed, trustedKeyring(env));
  if (!selfCheck.ok) {
    throw new Error(`signed license fails the trusted keyring (${selfCheck.reason}); check LICENSE_SIGNING_KEY's kid and the keyring`);
  }
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
  if (!stored) return { license: null, created: false };
  return { license: stored, created: (res.meta?.changes ?? 0) > 0 && stored.id === payload.id };
}

/** One alert per order (email_log kind "alert-issue") when issuing its license failed; never throws. */
async function alertIssueFailure(env: Env, order: OrderRow, mode: LicenseMode, error: unknown): Promise<void> {
  try {
    const message = String(error instanceof Error ? error.message : error).slice(0, 500);
    await sendAlertOnce(env, "alert-issue", order.id, `${mode === "test" ? "[TEST] " : ""}license issuance failed for paid order ${order.id}`, [
      `Order:   ${order.id} (${order.company}, ${order.tier}/${order.option_id}), ${mode} mode`,
      `Payment: ${order.stripe_payment_intent ?? "(none)"}`,
      `Error:   ${message}`,
      "No license was issued. The webhook answered 500, so Stripe redelivers the event;",
      "fix the configuration (LICENSE_SIGNING_KEY, the keyring, D1) and the next delivery issues the license.",
      "This alert is sent once per order.",
    ]);
  } catch (e) {
    console.error("[issue] issuance alert failed:", e);
  }
}

const prefixOf = (row: LicenseRow) => (row.mode === "test" ? "[TEST] " : "");

/**
 * Emails the license to the buyer unless that already happened. A claim on
 * the row keeps concurrent deliveries from sending it twice (a claim older
 * than five minutes is treated as abandoned). When Resend does not accept
 * the email, the claim is released, the operator is alerted and this throws:
 * the webhook answers 500, Stripe redelivers the event and the next delivery
 * sends the email. Without Resend configured (status "skipped") it only logs.
 * A fresh claim held by another delivery also throws (500), so that delivery
 * is not recorded as processed before the email is out. A test deployment
 * emails only buyers on TEST_EMAIL_ALLOWLIST; for anyone else it records a
 * skipped attempt and marks the license emailed, so redeliveries stop.
 */
async function deliverBuyerEmail(env: Env, order: OrderRow, row: LicenseRow, siteUrl: string): Promise<void> {
  if (row.emailed_at) return;
  const now = nowIso();
  const stale = new Date(Date.now() - 5 * 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const claim = await env.DB.prepare(
    `UPDATE licenses SET email_claimed_at = ?1
     WHERE id = ?2 AND emailed_at IS NULL AND (email_claimed_at IS NULL OR email_claimed_at < ?3)`,
  )
    .bind(now, row.id, stale)
    .run();
  if ((claim.meta?.changes ?? 0) === 0) {
    // Sent already, or another delivery holds a fresh claim and is sending it.
    const current = await licenseById(env.DB, row.id);
    if (!current || current.emailed_at) return;
    throw new Error(`buyer email for ${row.id} is being sent by another delivery`);
  }

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

  const msg: OutgoingEmail = {
    to: [order.buyer_email],
    subject: `${prefixOf(row)}Your PineForge Codegen commercial license ${row.id}`,
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
      "The invoice for this payment comes from Stripe in a separate email.",
      "",
      `Questions: ${env.LICENSE_NOTIFY_TO ?? "enterprise@pineforge.dev"}`,
    ].join("\n"),
    attachments: [{ filename: `${row.id}.json`, content: b64Encode(new TextEncoder().encode(signedJson + "\n")) }],
    kind: "license",
    relatedId: row.id,
  };

  if (deploymentMode(env) === "test" && !isAllowlisted(env.TEST_EMAIL_ALLOWLIST ?? "", order.buyer_email)) {
    await recordEmail(env, msg, { status: "skipped", providerId: null, error: "not in TEST_EMAIL_ALLOWLIST" });
    await env.DB.prepare("UPDATE licenses SET emailed_at = ?1 WHERE id = ?2").bind(nowIso(), row.id).run();
    return;
  }

  const result = await sendEmail(env, msg);
  if (result.status === "sent") {
    await env.DB.prepare("UPDATE licenses SET emailed_at = ?1 WHERE id = ?2").bind(nowIso(), row.id).run();
    return;
  }
  await env.DB.prepare("UPDATE licenses SET email_claimed_at = NULL WHERE id = ?1").bind(row.id).run();
  // Not configured (local development without Resend): nothing a retry would fix.
  if (result.status === "skipped") return;
  await sendAlert(
    env,
    `license email to the buyer failed (${row.id})`,
    [
      `License: ${row.id} (${row.mode}), order ${order.id} (${order.company})`,
      `Result:  ${result.status}: ${result.error ?? ""}`,
      "The webhook answered 500 so Stripe redelivers the event; the next delivery retries the email.",
      `The buyer can also fetch the license from the order page or ${siteUrl}/certificate/${row.id}.`,
    ],
    row.id,
  );
  throw new Error(`buyer email for ${row.id} not accepted (${result.status})`);
}

/** The sale notice to LICENSE_NOTIFY_TO, sent once by the delivery that created the license. */
async function sendSaleNotice(env: Env, order: OrderRow, row: LicenseRow, siteUrl: string): Promise<void> {
  const certificateUrl = `${siteUrl}/certificate/${row.id}`;
  const notifyTo = (env.LICENSE_NOTIFY_TO ?? "").trim();
  await sendEmail(env, {
    to: notifyTo ? [notifyTo] : [],
    subject: `${prefixOf(row)}License sale: ${row.id} — ${order.company} (${order.tier}/${order.option_id})`,
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
