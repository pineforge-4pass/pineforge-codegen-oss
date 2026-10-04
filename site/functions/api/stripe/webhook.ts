// POST /api/stripe/webhook
//
// Refuses a body over 1 MiB (413) unread, verifies the Stripe-Signature over
// the raw body (400 and no side effects when it is missing or wrong), rejects
// events whose livemode differs from this deployment's mode, skips event ids
// already processed, processes the event and only then records its id: a
// failed run answers 500, Stripe retries, and processing the same event again
// is safe. Disputes: a new one alerts the operator once; a lost one revokes
// the order's license (reason dispute_lost) and alerts; a won one is recorded.
import { Stripe, idOf } from "../../../server/stripe.ts";
import { deploymentMode, liveBlockReasons, siteUrl, type Env } from "../../../server/env.ts";
import { apiError, json, readTextCapped } from "../../../server/http.ts";
import { licenseByOrder, orderById, orderByPaymentIntent, orderBySession, type OrderRow } from "../../../server/db.ts";
import { issueLicense } from "../../../server/issue.ts";
import { alertOnceOrThrow, sendAlert } from "../../../server/email.ts";
import { nowIso } from "../../../lib/dates.ts";

type Session = Stripe.Checkout.Session;
type Charge = Stripe.Charge;
type Dispute = Stripe.Dispute;

/** Stripe event payloads are far smaller; anything larger is refused unread. */
const MAX_BODY_BYTES = 1024 * 1024;

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  const raw = await readTextCapped(request, MAX_BODY_BYTES);
  if (raw === null) return apiError(413, "payload_too_large");
  const signature = request.headers.get("Stripe-Signature");
  if (!signature) return apiError(400, "invalid_signature");
  const secret = (env.STRIPE_WEBHOOK_SECRET ?? "").trim();
  if (!secret) {
    console.error("[webhook] STRIPE_WEBHOOK_SECRET is not set");
    return apiError(500, "server_error");
  }

  let event: Stripe.Event;
  try {
    event = await Stripe.webhooks.constructEventAsync(raw, signature, secret, undefined, Stripe.createSubtleCryptoProvider());
  } catch {
    return apiError(400, "invalid_signature");
  }
  if (event.livemode !== (deploymentMode(env) === "live")) return apiError(400, "livemode_mismatch");

  try {
    const seen = await env.DB.prepare("SELECT id FROM stripe_events WHERE id = ?1").bind(event.id).first();
    if (seen) return json(200, { received: true });

    await handle(env, event, siteUrl(env, request));

    await env.DB.prepare(
      "INSERT INTO stripe_events (id, type, livemode, received_at) VALUES (?1, ?2, ?3, ?4) ON CONFLICT (id) DO NOTHING",
    )
      .bind(event.id, event.type, event.livemode ? 1 : 0, nowIso())
      .run();
    return json(200, { received: true });
  } catch (e) {
    console.error(`[webhook] ${event.type} ${event.id} failed:`, e);
    return apiError(500, "server_error");
  }
};

async function handle(env: Env, event: Stripe.Event, site: string): Promise<void> {
  switch (event.type) {
    case "checkout.session.completed":
    case "checkout.session.async_payment_succeeded":
      return sessionPaid(env, event.data.object, site);
    case "checkout.session.async_payment_failed":
      return sessionClosed(env, event.data.object, "failed");
    case "checkout.session.expired":
      return sessionClosed(env, event.data.object, "expired");
    case "charge.refunded":
      return chargeRefunded(env, event.data.object);
    case "charge.dispute.created":
      return disputeCreated(env, event.data.object);
    case "charge.dispute.closed":
      return disputeClosed(env, event.data.object);
    default:
      return;
  }
}

/** The order a session belongs to: same session id AND client_reference_id. */
async function matchOrder(env: Env, session: Session): Promise<OrderRow | null> {
  const bySession = await orderBySession(env.DB, session.id);
  if (bySession && session.client_reference_id === bySession.id) return bySession;
  const ref = session.client_reference_id;
  const byRef = ref ? await orderById(env.DB, ref) : null;
  if (bySession || byRef) {
    await sendAlert(
      env,
      `Checkout session ${session.id} does not match its order`,
      [
        `Session:             ${session.id}`,
        `client_reference_id: ${ref ?? "(none)"}`,
        `Order by session:    ${bySession?.id ?? "(none)"}`,
        `Order by reference:  ${byRef?.id ?? "(none)"} (its session: ${byRef?.stripe_session_id ?? "(none)"})`,
        "No license was issued. Check the payment in the Stripe dashboard.",
      ],
      bySession?.id ?? byRef?.id ?? null,
    );
  } else {
    console.warn(`[webhook] session ${session.id} belongs to no order of this site`);
  }
  return null;
}

async function sessionPaid(env: Env, session: Session, site: string): Promise<void> {
  // A delayed payment method completes the session unpaid; its
  // async_payment_succeeded event comes later.
  if (session.payment_status !== "paid") return;
  const order = await matchOrder(env, session);
  if (!order) return;
  // Only a pending (or failed/expired, then paid) order can become paid; a
  // refunded, mismatched or disputed one never issues.
  if (order.status === "refunded" || order.status === "mismatch" || order.status === "disputed") return;

  const now = nowIso();
  const paymentIntent = idOf(session.payment_intent);
  const customer = idOf(session.customer);
  const invoice = idOf(session.invoice);
  const blocked = session.livemode ? liveBlockReasons() : [];

  if (order.status !== "paid") {
    const currency = (session.currency ?? "").toLowerCase();
    if (session.amount_subtotal !== order.amount_subtotal || currency !== order.currency.toLowerCase()) {
      const res = await env.DB.prepare(
        `UPDATE orders SET status = 'mismatch', stripe_payment_intent = ?1, stripe_customer = ?2, stripe_invoice = ?3,
           livemode = ?4, updated_at = ?5
         WHERE id = ?6 AND status IN ('pending', 'failed', 'expired')`,
      )
        .bind(paymentIntent, customer, invoice, session.livemode ? 1 : 0, now, order.id)
        .run();
      if ((res.meta?.changes ?? 0) > 0) {
        await sendAlert(
          env,
          `amount mismatch on order ${order.id}`,
          [
            `Order:    ${order.id} (${order.company}, ${order.tier}/${order.option_id})`,
            `Expected: ${order.amount_subtotal} ${order.currency}`,
            `Paid:     ${session.amount_subtotal ?? "(none)"} ${session.currency ?? "(none)"} (subtotal)`,
            `Session:  ${session.id}, payment ${paymentIntent ?? "(none)"}`,
            "No license was issued; the order is marked mismatch. Refund or resolve it in Stripe.",
          ],
          order.id,
        );
      }
      return;
    }
    const res = await env.DB.prepare(
      `UPDATE orders SET status = 'paid', paid_at = ?1, stripe_payment_intent = ?2, stripe_customer = ?3,
         stripe_invoice = ?4, livemode = ?5, updated_at = ?1
       WHERE id = ?6 AND status IN ('pending', 'failed', 'expired')`,
    )
      .bind(now, paymentIntent, customer, invoice, session.livemode ? 1 : 0, order.id)
      .run();
    if (blocked.length > 0 && (res.meta?.changes ?? 0) > 0) {
      await sendAlert(
        env,
        `live payment received while live payments are blocked (order ${order.id})`,
        [
          `Order:   ${order.id} (${order.company}, ${order.tier}/${order.option_id})`,
          `Session: ${session.id}, payment ${paymentIntent ?? "(none)"}`,
          ...blocked.map((r) => `Blocked: ${r}`),
          "No license was issued. Resolve the items above, then issue or refund manually.",
        ],
        order.id,
      );
    }
  }

  // Never issue from a livemode event while live payments are blocked.
  if (blocked.length > 0) return;
  const fresh = await orderById(env.DB, order.id);
  if (!fresh || fresh.status !== "paid") return;
  await issueLicense(env, fresh, session.livemode ? "live" : "test", site);
}

async function sessionClosed(env: Env, session: Session, status: "failed" | "expired"): Promise<void> {
  const order = await matchOrder(env, session);
  if (!order) return;
  await env.DB.prepare("UPDATE orders SET status = ?1, updated_at = ?2 WHERE id = ?3 AND status = 'pending'")
    .bind(status, nowIso(), order.id)
    .run();
}

async function chargeRefunded(env: Env, charge: Charge): Promise<void> {
  const paymentIntent = idOf(charge.payment_intent);
  let order = paymentIntent ? await orderByPaymentIntent(env.DB, paymentIntent) : null;
  if (!order) {
    // The refund can arrive before the checkout event recorded the payment.
    const ref = charge.metadata?.order_id;
    const byRef = ref ? await orderById(env.DB, ref) : null;
    if (byRef && (!byRef.stripe_payment_intent || byRef.stripe_payment_intent === paymentIntent)) order = byRef;
  }
  if (!order) {
    console.warn(`[webhook] charge ${charge.id} belongs to no order of this site`);
    return;
  }
  const now = nowIso();
  const refunded = Math.max(0, Math.trunc(charge.amount_refunded ?? 0));
  const full = charge.refunded === true || refunded >= charge.amount;
  if (!full) {
    await env.DB.prepare(
      "UPDATE orders SET amount_refunded = MAX(amount_refunded, ?1), updated_at = ?2 WHERE id = ?3",
    )
      .bind(refunded, now, order.id)
      .run();
    return;
  }
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE orders SET status = 'refunded', amount_refunded = MAX(amount_refunded, ?1),
         refunded_at = COALESCE(refunded_at, ?2), stripe_payment_intent = COALESCE(stripe_payment_intent, ?3),
         updated_at = ?2
       WHERE id = ?4`,
    ).bind(refunded, now, paymentIntent, order.id),
    env.DB.prepare(
      "UPDATE licenses SET status = 'revoked', revoked_at = ?1, revoke_reason = 'refund' WHERE order_id = ?2 AND status = 'active'",
    ).bind(now, order.id),
  ]);
}

/** The order a dispute's payment belongs to, or null (logged) when it is not one of this site's. */
async function disputeOrder(env: Env, dispute: Dispute): Promise<OrderRow | null> {
  const paymentIntent = idOf(dispute.payment_intent);
  const order = paymentIntent ? await orderByPaymentIntent(env.DB, paymentIntent) : null;
  if (!order) console.warn(`[webhook] dispute ${dispute.id} belongs to no order of this site`);
  return order;
}

function disputeLines(dispute: Dispute, order: OrderRow, licenseId: string | null): string[] {
  return [
    `Dispute: ${dispute.id} (${dispute.status}, reason ${dispute.reason})`,
    `Amount:  ${dispute.amount} ${dispute.currency} (minor units)`,
    `Charge:  ${idOf(dispute.charge) ?? "(none)"}, payment ${idOf(dispute.payment_intent) ?? "(none)"}`,
    `Order:   ${order.id} (${order.company}, ${order.tier}/${order.option_id}), buyer ${order.buyer_email}`,
    `License: ${licenseId ?? "(none issued)"}`,
  ];
}

/** A new dispute: one alert per dispute (email_log kind "alert-dispute"). */
async function disputeCreated(env: Env, dispute: Dispute): Promise<void> {
  const order = await disputeOrder(env, dispute);
  if (!order) return;
  const license = await licenseByOrder(env.DB, order.id);
  await alertOnceOrThrow(env, "alert-dispute", dispute.id, `payment disputed on order ${order.id} (${dispute.id})`, [
    ...disputeLines(dispute, order, license?.id ?? null),
    "Respond in the Stripe dashboard before the evidence deadline. The license stays active",
    "unless the dispute is lost.",
  ]);
}

/** A closed dispute: lost revokes the order's license (reason dispute_lost) and alerts once; won is only recorded. */
async function disputeClosed(env: Env, dispute: Dispute): Promise<void> {
  if (dispute.status !== "lost") {
    console.log(`[webhook] dispute ${dispute.id} closed: ${dispute.status}`);
    return;
  }
  const order = await disputeOrder(env, dispute);
  if (!order) return;
  // One batch: the order becomes `disputed` (so no later delivery or manual
  // redelivery can issue a license for it) and any active license is revoked.
  const now = nowIso();
  await env.DB.batch([
    env.DB.prepare("UPDATE orders SET status = 'disputed', updated_at = ?1 WHERE id = ?2").bind(now, order.id),
    env.DB.prepare(
      "UPDATE licenses SET status = 'revoked', revoked_at = ?1, revoke_reason = 'dispute_lost' WHERE order_id = ?2 AND status = 'active'",
    ).bind(now, order.id),
  ]);
  const license = await licenseByOrder(env.DB, order.id);
  await alertOnceOrThrow(env, "alert-dispute-lost", dispute.id, `dispute lost on order ${order.id} (${dispute.id})`, [
    ...disputeLines(dispute, order, license?.id ?? null),
    license
      ? `The license is revoked (${license.status}, reason ${license.revoke_reason ?? "(none)"}).`
      : "No license was issued for this order.",
  ]);
}
