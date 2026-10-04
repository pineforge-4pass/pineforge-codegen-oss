// POST /api/checkout
//
// Validates the plan (priced from config/commerce.json, never from the
// client) and the buyer fields, rate-limits per client IP, refuses live
// payments while the live guard holds (503 before any Stripe call), stores a
// pending order and creates a Stripe Checkout Session for it.
import { commerce } from "../../lib/commerce-config.ts";
import { findPlan, productName, scopeOf, scopeSummary } from "../../lib/commerce.ts";
import { HONEYPOT_FIELD, validateCheckout } from "../../lib/validate.ts";
import { AGREEMENT_SHA256, AGREEMENT_VERSION } from "../../lib/generated/build-info.ts";
import { nowIso } from "../../lib/dates.ts";
import { deploymentMode, liveBlocked, safeLocale, siteUrl, type Env } from "../../server/env.ts";
import { BodyError, apiError, clientIp, json, randomId, readJson } from "../../server/http.ts";
import { CHECKOUT_LIMIT, allowRequest } from "../../server/rate-limit.ts";
import { rateLimitKey } from "../../lib/client-ip.ts";
import { Stripe, stripeClient } from "../../server/stripe.ts";

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  let body: unknown;
  try {
    body = await readJson(request, 16 * 1024, { requireJsonType: true });
  } catch (e) {
    if (!(e instanceof BodyError)) console.error("[checkout] reading the body failed:", e);
    return apiError(400, "invalid_json");
  }
  const fields = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  const site = siteUrl(env, request);

  // Honeypot: answer like a success, store nothing.
  const trap = fields[HONEYPOT_FIELD];
  if (typeof trap === "string" && trap.trim() !== "") {
    return json(200, { url: `${site}/${safeLocale(fields.locale)}/` });
  }

  const plan = findPlan(commerce, fields.tier, fields.option);
  if (!plan) return apiError(400, "invalid_plan");
  const checked = validateCheckout(body);
  if (!checked.ok) return apiError(400, "invalid_fields", { fields: checked.errors });
  const input = checked.value;
  const locale = safeLocale(input.locale);

  try {
    if (!(await allowRequest(env.DB, CHECKOUT_LIMIT, rateLimitKey(clientIp(request))))) return apiError(429, "rate_limited");
  } catch (e) {
    console.error("[checkout] rate limit failed:", e);
    return apiError(500, "server_error");
  }

  if (liveBlocked(env)) return apiError(503, "live_payments_disabled");

  const { tier, option } = plan;
  const orderId = randomId("ord");
  const now = nowIso();
  const name = productName(commerce, tier, option);
  const amount = option.annual;
  const currency = commerce.currency.toLowerCase();
  const live = deploymentMode(env) === "live";

  try {
    await env.DB.prepare(
      `INSERT INTO orders (id, status, company, country, buyer_name, buyer_email, reference, tier, option_id,
         term_months, product_name, scope_summary, scope_json, amount_subtotal, currency, locale, livemode,
         agreement_version, agreement_sha256, created_at, updated_at)
       VALUES (?1, 'pending', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?19)`,
    )
      .bind(
        orderId,
        input.company,
        input.country,
        input.buyerName,
        input.buyerEmail,
        input.reference || null,
        tier.id,
        option.id,
        commerce.termMonths,
        name,
        scopeSummary(tier, option),
        JSON.stringify(scopeOf(option)),
        amount,
        currency,
        locale,
        live ? 1 : 0,
        AGREEMENT_VERSION,
        AGREEMENT_SHA256,
        now,
      )
      .run();
  } catch (e) {
    console.error("[checkout] order insert failed:", e);
    return apiError(500, "server_error");
  }

  const query = `tier=${encodeURIComponent(tier.id)}&option=${encodeURIComponent(option.id)}`;
  let session: Stripe.Checkout.Session;
  try {
    const stripe = stripeClient(env);
    session = await stripe.checkout.sessions.create(
      {
        mode: "payment",
        line_items: [
          {
            quantity: 1,
            price_data: { currency, unit_amount: amount, product_data: { name } },
          },
        ],
        customer_email: input.buyerEmail,
        client_reference_id: orderId,
        metadata: { order_id: orderId, tier: tier.id, option: option.id, term: String(commerce.termMonths) },
        payment_intent_data: { metadata: { order_id: orderId } },
        customer_creation: "always",
        invoice_creation: {
          enabled: true,
          invoice_data: {
            description: `${name}. Licensee: ${input.company} (${input.country}).`.slice(0, 1500),
            metadata: { order_id: orderId },
          },
        },
        automatic_tax: { enabled: (env.STRIPE_TAX ?? "").trim() === "on" },
        tax_id_collection: { enabled: true },
        billing_address_collection: "required",
        success_url: `${site}/${locale}/order/?session_id={CHECKOUT_SESSION_ID}`,
        cancel_url: `${site}/${locale}/checkout/?${query}&canceled=1`,
      },
      { idempotencyKey: orderId },
    );
  } catch (e) {
    console.error("[checkout] Stripe session create failed:", e);
    await markFailed(env, orderId);
    return apiError(502, "payment_provider_error");
  }
  if (!session.url) {
    console.error(`[checkout] session ${session.id} has no url`);
    await markFailed(env, orderId);
    return apiError(502, "payment_provider_error");
  }

  try {
    await env.DB.prepare("UPDATE orders SET stripe_session_id = ?1, updated_at = ?2 WHERE id = ?3")
      .bind(session.id, nowIso(), orderId)
      .run();
  } catch (e) {
    console.error("[checkout] storing the session id failed:", e);
    return apiError(500, "server_error");
  }
  return json(200, { url: session.url });
};

async function markFailed(env: Env, orderId: string): Promise<void> {
  try {
    await env.DB.prepare("UPDATE orders SET status = 'failed', updated_at = ?1 WHERE id = ?2 AND status = 'pending'")
      .bind(nowIso(), orderId)
      .run();
  } catch (e) {
    console.error("[checkout] marking the order failed failed:", e);
  }
}
