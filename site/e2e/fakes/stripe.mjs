#!/usr/bin/env node
// Local fake of the small part of the Stripe API this site uses. It exists only for the E2E suite:
// no Stripe account is available to it, so this server stands in for Stripe (the only mocking the
// suite does besides the Resend sink). It speaks what the official `stripe` npm client sends
// (form-encoded bodies with bracket keys, `Authorization: Bearer sk_test_...`, `Idempotency-Key`),
// serves a hosted-checkout look-alike at each session's `url`, and posts webhook events signed
// exactly like Stripe's (`Stripe-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<payload>")>`).
//
// Standalone:  node e2e/fakes/stripe.mjs --port 0 --webhook-url http://127.0.0.1:8788/api/stripe/webhook \
//                --webhook-secret whsec_...      (or env FAKE_STRIPE_PORT / _WEBHOOK_URL / _WEBHOOK_SECRET)
// Module:      const fake = await startStripeFake({ port, webhookUrl, webhookSecret }); fake.url; await fake.close();
//
// Stripe API:  POST /v1/checkout/sessions, GET /v1/checkout/sessions/:id, GET /v1/checkout/sessions/:id/line_items
// Hosted page: GET /pay/:id, POST /pay/:id (4242 4242 4242 4242 pays; 4000 0000 0000 0002 is declined;
//              4000 0000 0000 0077 pays but its webhook is delivered `deferMs` (default 3000) AFTER the redirect)
// Control:     GET  /__control/health
//              GET  /__control/sessions            -> { sessions: [{ session, params, idempotencyKey }] }
//              GET  /__control/sessions/:id        -> { session, params, idempotencyKey, charge }
//              GET  /__control/requests            -> { requests: [{ method, path, auth, idempotencyKey, status }] }
//              GET  /__control/events              -> { events: [{ event, delivery }] }
//              POST /__control/refund      { payment_intent, amount? }                 -> { charge, event, delivery }
//              POST /__control/send-event  { type, object, signature?, livemode?, url?, id? } -> { event, delivery }
//                   signature: "valid" (default) | "bad" | "none" | "stale" | "wrong-secret"
//              POST /__control/redeliver   { session_id } -> { event, delivery }: the SAME completed event
//                   (same id and payload) posted again with a fresh signature, as Stripe retries it
import http from "node:http";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

export const FAKE_API_VERSION = "2026-09-30.endive";
const TEST_CARD_OK = "4242424242424242";
const TEST_CARD_DECLINED = "4000000000000002";
const TEST_CARD_DEFERRED = "4000000000000077";

function rand(n = 24) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = crypto.randomBytes(n);
  let out = "";
  for (const b of bytes) out += alphabet[b % alphabet.length];
  return out;
}

const now = () => Math.floor(Date.now() / 1000);

// "a[b][0][c]=1" -> { a: { b: [ { c: "1" } ] } }. Brackets may arrive raw or percent-encoded.
export function parseStripeForm(body) {
  const root = {};
  const params = new URLSearchParams(body);
  for (const [rawKey, value] of params) {
    const parts = [];
    const m = /^([^[\]]+)(.*)$/.exec(rawKey);
    if (!m) continue;
    parts.push(m[1]);
    const re = /\[([^\]]*)\]/g;
    let g;
    while ((g = re.exec(m[2])) !== null) parts.push(g[1]);
    let node = root;
    for (let i = 0; i < parts.length; i += 1) {
      const key = parts[i];
      const last = i === parts.length - 1;
      const nextIsIndex = !last && /^\d*$/.test(parts[i + 1]);
      if (Array.isArray(node)) {
        const idx = key === "" ? node.length : Number(key);
        if (last) node[idx] = value;
        else node = node[idx] ?? (node[idx] = nextIsIndex ? [] : {});
      } else if (last) {
        node[key] = value;
      } else {
        node = node[key] ?? (node[key] = nextIsIndex ? [] : {});
      }
    }
  }
  return root;
}

export function signStripePayload(payload, secret, timestamp = now()) {
  const v1 = crypto.createHmac("sha256", secret).update(`${timestamp}.${payload}`, "utf8").digest("hex");
  return `t=${timestamp},v1=${v1}`;
}

function bool(v) {
  return v === true || v === "true";
}

function int(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function money(amount, currency) {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: String(currency || "usd").toUpperCase() }).format(amount / 100);
  } catch {
    return `${(amount / 100).toFixed(2)} ${String(currency).toUpperCase()}`;
  }
}

function stripeError(res, status, message, type = "invalid_request_error", extra = {}) {
  sendJson(res, status, { error: { type, message, ...extra } });
}

function sendJson(res, status, body, headers = {}) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "Request-Id": `req_${rand(14)}`,
    "Stripe-Version": FAKE_API_VERSION,
    ...headers,
  });
  res.end(text);
}

function sendHtml(res, status, html) {
  res.writeHead(status, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
  res.end(html);
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

function lineItemsFrom(params) {
  const raw = Array.isArray(params.line_items) ? params.line_items : Object.values(params.line_items ?? {});
  return raw.filter(Boolean).map((li) => {
    const pd = li.price_data ?? {};
    const quantity = li.quantity === undefined ? 1 : int(li.quantity);
    const unit = int(pd.unit_amount ?? pd.unit_amount_decimal);
    const currency = String(pd.currency ?? "").toLowerCase();
    const productName = pd.product_data?.name ?? "";
    return {
      id: `li_${rand(24)}`,
      object: "item",
      amount_discount: 0,
      amount_subtotal: unit * quantity,
      amount_tax: 0,
      amount_total: unit * quantity,
      currency,
      description: productName,
      quantity,
      price: {
        id: `price_${rand(24)}`,
        object: "price",
        active: false,
        currency,
        livemode: false,
        product: `prod_${rand(14)}`,
        type: "one_time",
        unit_amount: unit,
        unit_amount_decimal: String(unit),
        product_data: pd.product_data ?? null,
      },
    };
  });
}

export async function startStripeFake(options = {}) {
  const opts = {
    port: Number(options.port ?? 0),
    host: options.host ?? "127.0.0.1",
    webhookUrl: options.webhookUrl ?? "",
    webhookSecret: options.webhookSecret ?? "",
    log: options.log ?? (() => {}),
    deferMs: Number(options.deferMs ?? 3000),
  };
  const sessions = new Map(); // id -> { session, params, idempotencyKey, lineItems, charge }
  const byIdempotencyKey = new Map(); // key -> session id
  const byPaymentIntent = new Map(); // pi -> session id
  const requests = [];
  const events = [];
  let baseUrl = "";

  async function deliver(event, { signature = "valid", url } = {}) {
    const target = url || opts.webhookUrl;
    const payload = JSON.stringify(event, null, 2);
    const headers = {
      "Content-Type": "application/json; charset=utf-8",
      "User-Agent": "Stripe/1.0 (+https://stripe.com/docs/webhooks)",
      Accept: "*/*; q=0.5, application/xml",
    };
    const t = now();
    if (signature === "valid") headers["Stripe-Signature"] = signStripePayload(payload, opts.webhookSecret, t);
    else if (signature === "bad") {
      const good = signStripePayload(payload, opts.webhookSecret, t);
      const v1 = good.split("v1=")[1];
      const flipped = (v1[0] === "0" ? "1" : "0") + v1.slice(1);
      headers["Stripe-Signature"] = `t=${t},v1=${flipped}`;
    } else if (signature === "wrong-secret") headers["Stripe-Signature"] = signStripePayload(payload, `${opts.webhookSecret}x`, t);
    else if (signature === "stale") headers["Stripe-Signature"] = signStripePayload(payload, opts.webhookSecret, t - 3600);
    // "none": no header at all
    const record = { event, delivery: { url: target, signature, status: 0, body: "", error: null, at: new Date().toISOString() } };
    events.push(record);
    if (!target) {
      record.delivery.error = "no webhook url configured";
      return record.delivery;
    }
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const r = await fetch(target, { method: "POST", headers, body: payload, signal: AbortSignal.timeout(30_000) });
        record.delivery.status = r.status;
        record.delivery.body = await r.text();
        record.delivery.error = null;
        break;
      } catch (err) {
        record.delivery.error = String(err?.cause?.code ?? err?.message ?? err);
        if (attempt < 3) await new Promise((r) => setTimeout(r, 500 * attempt));
      }
    }
    opts.log(`webhook ${event.type} ${event.id} -> ${record.delivery.status || record.delivery.error}`);
    return record.delivery;
  }

  function makeEvent(type, object, { livemode = false, id } = {}) {
    return {
      id: id ?? `evt_${rand(24)}`,
      object: "event",
      api_version: FAKE_API_VERSION,
      created: now(),
      data: { object },
      livemode,
      pending_webhooks: 1,
      request: { id: null, idempotency_key: null },
      type,
    };
  }

  function successUrlFor(session) {
    return String(session.success_url ?? "")
      .replaceAll("{CHECKOUT_SESSION_ID}", session.id)
      .replaceAll("%7BCHECKOUT_SESSION_ID%7D", session.id)
      .replaceAll("%7bCHECKOUT_SESSION_ID%7d", session.id);
  }

  function createSession(params, idempotencyKey) {
    const lineItems = lineItemsFrom(params);
    const subtotal = lineItems.reduce((a, li) => a + li.amount_subtotal, 0);
    const currency = lineItems[0]?.currency || String(params.currency ?? "usd").toLowerCase();
    const id = `cs_test_${rand(58)}`;
    const created = now();
    const session = {
      id,
      object: "checkout.session",
      after_expiration: null,
      allow_promotion_codes: null,
      amount_subtotal: subtotal,
      amount_total: subtotal,
      automatic_tax: { enabled: bool(params.automatic_tax?.enabled), liability: null, status: bool(params.automatic_tax?.enabled) ? "requires_location_inputs" : null },
      billing_address_collection: params.billing_address_collection ?? null,
      cancel_url: params.cancel_url ?? null,
      client_reference_id: params.client_reference_id ?? null,
      created,
      currency,
      customer: null,
      customer_creation: params.customer_creation ?? "if_required",
      customer_details: null,
      customer_email: params.customer_email ?? null,
      expires_at: created + 24 * 3600,
      invoice: null,
      invoice_creation: params.invoice_creation
        ? { enabled: bool(params.invoice_creation.enabled), invoice_data: params.invoice_creation.invoice_data ?? {} }
        : { enabled: false, invoice_data: {} },
      livemode: false,
      locale: params.locale ?? null,
      metadata: params.metadata ?? {},
      mode: params.mode ?? "payment",
      payment_intent: null,
      payment_method_types: ["card"],
      payment_status: "unpaid",
      status: "open",
      success_url: params.success_url ?? null,
      tax_id_collection: { enabled: bool(params.tax_id_collection?.enabled), required: "never" },
      total_details: { amount_discount: 0, amount_shipping: 0, amount_tax: 0 },
      ui_mode: "hosted",
      url: `${baseUrl}/pay/${id}`,
    };
    sessions.set(id, { session, params, idempotencyKey: idempotencyKey ?? null, lineItems, charge: null });
    if (idempotencyKey) byIdempotencyKey.set(idempotencyKey, id);
    return session;
  }

  function withExpand(entry, expand) {
    const out = { ...entry.session };
    if (expand.includes("line_items")) {
      out.line_items = { object: "list", data: entry.lineItems, has_more: false, url: `/v1/checkout/sessions/${entry.session.id}/line_items` };
    }
    return out;
  }

  function expandList(query) {
    const list = [];
    for (const [k, v] of query) if (k === "expand[]" || /^expand\[\d*\]$/.test(k)) list.push(v);
    return list;
  }

  async function handleApi(req, res, url) {
    const auth = req.headers.authorization ?? "";
    const key = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    const idem = req.headers["idempotency-key"] ?? null;
    const reqRecord = { method: req.method, path: url.pathname, auth: key ? `${key.slice(0, 8)}…` : "", idempotencyKey: idem, status: 0, at: new Date().toISOString() };
    requests.push(reqRecord);
    const finish = (status, body) => {
      reqRecord.status = status;
      sendJson(res, status, body, idem ? { "Idempotency-Key": idem } : {});
    };
    if (!key) {
      reqRecord.status = 401;
      return stripeError(res, 401, "You did not provide an API key. You need to provide your API key in the Authorization header, using Bearer auth (e.g. 'Authorization: Bearer YOUR_SECRET_KEY').");
    }
    if (!key.startsWith("sk_test_") && !key.startsWith("rk_test_")) {
      reqRecord.status = 401;
      return stripeError(res, 401, `Invalid API Key provided: ${key.slice(0, 8)}********. This fake accepts test-mode keys only.`);
    }
    const body = req.method === "POST" ? await readBody(req) : "";
    if (req.method === "POST" && url.pathname === "/v1/checkout/sessions") {
      if (idem && byIdempotencyKey.has(idem)) {
        return finish(200, sessions.get(byIdempotencyKey.get(idem)).session);
      }
      const params = parseStripeForm(body);
      if (!params.success_url) return finish(400, { error: { type: "invalid_request_error", message: "Missing required param: success_url.", param: "success_url" } });
      const items = lineItemsFrom(params);
      if (items.length === 0) return finish(400, { error: { type: "invalid_request_error", message: "Missing required param: line_items.", param: "line_items" } });
      if (items.some((li) => !li.currency || li.price.unit_amount <= 0)) {
        return finish(400, { error: { type: "invalid_request_error", message: "line_items[0][price_data] needs a currency and a positive unit_amount.", param: "line_items" } });
      }
      const session = createSession(params, idem);
      opts.log(`created ${session.id} ${session.amount_subtotal} ${session.currency}`);
      return finish(200, session);
    }
    let m = /^\/v1\/checkout\/sessions\/([^/]+)$/.exec(url.pathname);
    if (req.method === "GET" && m) {
      const entry = sessions.get(m[1]);
      if (!entry) return finish(404, { error: { type: "invalid_request_error", code: "resource_missing", message: `No such checkout.session: '${m[1]}'`, param: "session" } });
      return finish(200, withExpand(entry, expandList(url.searchParams)));
    }
    m = /^\/v1\/checkout\/sessions\/([^/]+)\/line_items$/.exec(url.pathname);
    if (req.method === "GET" && m) {
      const entry = sessions.get(m[1]);
      if (!entry) return finish(404, { error: { type: "invalid_request_error", code: "resource_missing", message: `No such checkout.session: '${m[1]}'` } });
      return finish(200, { object: "list", data: entry.lineItems, has_more: false, url: url.pathname });
    }
    return finish(404, { error: { type: "invalid_request_error", message: `Unrecognized request URL (${req.method}: ${url.pathname}). This fake implements only what the site uses.` } });
  }

  function hostedPage(entry, { error = "", values = {} } = {}) {
    const s = entry.session;
    const item = entry.lineItems[0];
    const total = money(s.amount_total, s.currency);
    const email = values.email ?? s.customer_email ?? "";
    const countries = [["US", "United States"], ["GB", "United Kingdom"], ["DE", "Germany"], ["SG", "Singapore"], ["TW", "Taiwan"], ["JP", "Japan"]];
    const country = values.country ?? "US";
    const closed = s.status !== "open";
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Checkout (test fake)</title>
<style>
  *{box-sizing:border-box} body{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1a1f36;background:#fff}
  .wrap{display:grid;grid-template-columns:1fr 1fr;min-height:100vh}
  .summary{background:#f6f8fa;padding:64px 48px 32px 15%;border-right:1px solid #e3e8ee}
  .pay{padding:64px 15% 32px 48px}
  .badge{display:inline-block;font-size:12px;font-weight:600;color:#a04100;background:#ffde92;padding:2px 6px;border-radius:4px;margin-left:8px}
  .merchant{font-weight:600;font-size:15px;color:#3c4257}
  .total{font-size:36px;font-weight:600;margin:8px 0 24px}
  .line{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid #e3e8ee;font-size:14px}
  h1{font-size:20px;font-weight:600;margin:0 0 20px}
  label{display:block;font-size:13px;color:#3c4257;margin:16px 0 6px}
  input,select{width:100%;font:inherit;padding:10px 12px;border:1px solid #c9d1da;border-radius:6px;background:#fff;color:#1a1f36;min-height:44px}
  input:focus,select:focus{outline:2px solid #0a2540;outline-offset:1px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  button{width:100%;margin-top:24px;min-height:48px;font:inherit;font-weight:600;color:#fff;background:#0a2540;border:0;border-radius:6px;cursor:pointer}
  .err{margin:16px 0 0;padding:10px 12px;border-radius:6px;background:#fdecec;color:#8a1c1c;font-size:14px}
  .note{font-size:12px;color:#4f566b;margin-top:24px}
  @media (max-width:800px){.wrap{grid-template-columns:1fr}.summary,.pay{padding:24px 20px;border-right:0}.total{font-size:28px}}
</style>
</head>
<body>
<div class="wrap">
  <section class="summary" aria-label="Order summary" data-testid="fake-order-summary">
    <div class="merchant">PineForge <span class="badge">TEST MODE</span></div>
    <div>${escapeHtml(item?.description ?? "")}</div>
    <div class="total" data-testid="fake-total">${escapeHtml(total)}</div>
    ${entry.lineItems
      .map((li) => `<div class="line"><span>${escapeHtml(li.description)}<br><small>Qty ${li.quantity}</small></span><span>${escapeHtml(money(li.amount_total, li.currency))}</span></div>`)
      .join("")}
    <div class="line"><strong>Total due</strong><strong>${escapeHtml(total)}</strong></div>
  </section>
  <main class="pay">
    <h1>${closed ? "This checkout session is complete" : "Pay with card"}</h1>
    ${error ? `<p class="err" role="alert" data-testid="fake-card-error">${escapeHtml(error)}</p>` : ""}
    ${
      closed
        ? `<p><a href="${escapeHtml(successUrlFor(s))}">Return to the merchant</a></p>`
        : `<form method="post" action="/pay/${escapeHtml(s.id)}" novalidate>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="email" value="${escapeHtml(email)}">
      <label for="cardnumber">Card number</label>
      <input id="cardnumber" name="cardNumber" inputmode="numeric" autocomplete="cc-number" placeholder="1234 1234 1234 1234" value="${escapeHtml(values.cardNumber ?? "")}">
      <div class="row">
        <div><label for="exp">Expiration (MM / YY)</label>
        <input id="exp" name="expiry" autocomplete="cc-exp" placeholder="MM / YY" value="${escapeHtml(values.expiry ?? "")}"></div>
        <div><label for="cvc">CVC</label>
        <input id="cvc" name="cvc" inputmode="numeric" autocomplete="cc-csc" placeholder="CVC" value="${escapeHtml(values.cvc ?? "")}"></div>
      </div>
      <label for="name">Cardholder name</label>
      <input id="name" name="name" autocomplete="cc-name" placeholder="Full name on card" value="${escapeHtml(values.name ?? "")}">
      <label for="country">Country or region</label>
      <select id="country" name="country" autocomplete="country">
        ${countries.map(([code, label]) => `<option value="${code}"${code === country ? " selected" : ""}>${label}</option>`).join("")}
      </select>
      <button type="submit">Pay</button>
    </form>`
    }
    <p class="note">Local test fake of Stripe Checkout used by the E2E suite. No real payment is taken.</p>
  </main>
</div>
</body>
</html>`;
  }

  function validateCard(values) {
    const number = String(values.cardNumber ?? "").replace(/[\s-]/g, "");
    if (!values.email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) return "Your email address is invalid.";
    if (!number) return "Your card number is incomplete.";
    const exp = /^\s*(\d{1,2})\s*\/\s*(\d{2})\s*$/.exec(String(values.expiry ?? ""));
    if (!exp) return "Your card's expiration date is incomplete.";
    const month = Number(exp[1]);
    const year = 2000 + Number(exp[2]);
    const today = new Date();
    if (month < 1 || month > 12 || year < today.getUTCFullYear() || (year === today.getUTCFullYear() && month < today.getUTCMonth() + 1)) {
      return "Your card's expiration date is in the past.";
    }
    if (!/^\d{3,4}$/.test(String(values.cvc ?? "").trim())) return "Your card's security code is incomplete.";
    if (!String(values.name ?? "").trim()) return "Your name is required.";
    if (number === TEST_CARD_DECLINED) return "Your card was declined.";
    if (number !== TEST_CARD_OK && number !== TEST_CARD_DEFERRED) return "Your card number is incorrect.";
    return "";
  }

  async function payHostedSession(entry, values, { defer = false } = {}) {
    const s = entry.session;
    s.status = "complete";
    s.payment_status = "paid";
    s.payment_intent = `pi_${rand(24)}`;
    s.customer = `cus_${rand(14)}`;
    s.customer_details = {
      address: { city: null, country: values.country ?? "US", line1: null, line2: null, postal_code: null, state: null },
      email: values.email,
      name: values.name,
      phone: null,
      tax_exempt: "none",
      tax_ids: [],
    };
    if (s.invoice_creation?.enabled) s.invoice = `in_${rand(24)}`;
    byPaymentIntent.set(s.payment_intent, s.id);
    entry.charge = {
      id: `ch_${rand(24)}`,
      object: "charge",
      amount: s.amount_total,
      amount_captured: s.amount_total,
      amount_refunded: 0,
      billing_details: { email: values.email, name: values.name, address: s.customer_details.address, phone: null },
      captured: true,
      created: now(),
      currency: s.currency,
      customer: s.customer,
      livemode: false,
      metadata: {},
      paid: true,
      payment_intent: s.payment_intent,
      refunded: false,
      status: "succeeded",
    };
    const event = makeEvent("checkout.session.completed", structuredClone(s));
    entry.completedEvent = event;
    if (defer) {
      setTimeout(() => deliver(event).catch((err) => opts.log(`deferred delivery failed: ${err}`)), opts.deferMs);
      return null;
    }
    return deliver(event);
  }

  async function handlePay(req, res, url, id) {
    const entry = sessions.get(id);
    if (!entry) return sendHtml(res, 404, `<!doctype html><html lang="en"><title>Not found</title><main><h1>Checkout session not found</h1></main></html>`);
    if (req.method === "GET") return sendHtml(res, 200, hostedPage(entry));
    const values = Object.fromEntries(new URLSearchParams(await readBody(req)));
    if (entry.session.status !== "open") {
      res.writeHead(303, { Location: successUrlFor(entry.session) });
      return res.end();
    }
    const error = validateCard(values);
    if (error) return sendHtml(res, 402, hostedPage(entry, { error, values: { ...values, cardNumber: values.cardNumber } }));
    const defer = String(values.cardNumber ?? "").replace(/[\s-]/g, "") === TEST_CARD_DEFERRED;
    await payHostedSession(entry, values, { defer });
    res.writeHead(303, { Location: successUrlFor(entry.session) });
    res.end();
  }

  async function handleControl(req, res, url) {
    const path = url.pathname.slice("/__control".length);
    if (path === "/health") return sendJson(res, 200, { ok: true, webhookUrl: opts.webhookUrl });
    if (req.method === "GET" && path === "/sessions") {
      return sendJson(res, 200, { sessions: [...sessions.values()].map((e) => ({ session: e.session, params: e.params, idempotencyKey: e.idempotencyKey, charge: e.charge })) });
    }
    let m = /^\/sessions\/([^/]+)$/.exec(path);
    if (req.method === "GET" && m) {
      const e = sessions.get(m[1]);
      if (!e) return sendJson(res, 404, { error: "not_found" });
      return sendJson(res, 200, { session: e.session, params: e.params, idempotencyKey: e.idempotencyKey, charge: e.charge, lineItems: e.lineItems });
    }
    if (req.method === "GET" && path === "/requests") return sendJson(res, 200, { requests });
    if (req.method === "GET" && path === "/events") return sendJson(res, 200, { events });
    if (req.method === "POST" && path === "/refund") {
      let body;
      try {
        body = JSON.parse((await readBody(req)) || "{}");
      } catch {
        return sendJson(res, 400, { error: "invalid_json" });
      }
      const sid = byPaymentIntent.get(body.payment_intent);
      const e = sid ? sessions.get(sid) : null;
      if (!e?.charge) return sendJson(res, 404, { error: "unknown_payment_intent" });
      const remaining = e.charge.amount - e.charge.amount_refunded;
      const amount = body.amount === undefined || body.amount === null ? remaining : Math.min(int(body.amount), remaining);
      if (amount <= 0) return sendJson(res, 400, { error: "already_refunded" });
      e.charge.amount_refunded += amount;
      e.charge.refunded = e.charge.amount_refunded >= e.charge.amount;
      const event = makeEvent("charge.refunded", structuredClone(e.charge));
      event.data.previous_attributes = { amount_refunded: e.charge.amount_refunded - amount, refunded: false };
      const delivery = await deliver(event);
      return sendJson(res, 200, { charge: e.charge, event, delivery });
    }
    if (req.method === "POST" && path === "/redeliver") {
      let body;
      try {
        body = JSON.parse((await readBody(req)) || "{}");
      } catch {
        return sendJson(res, 400, { error: "invalid_json" });
      }
      const e = sessions.get(body.session_id);
      if (!e?.completedEvent) return sendJson(res, 404, { error: "no_completed_event" });
      const delivery = await deliver(e.completedEvent, { signature: body.signature ?? "valid" });
      return sendJson(res, 200, { event: e.completedEvent, delivery });
    }
    if (req.method === "POST" && path === "/send-event") {
      let body;
      try {
        body = JSON.parse((await readBody(req)) || "{}");
      } catch {
        return sendJson(res, 400, { error: "invalid_json" });
      }
      if (!body.type || typeof body.object !== "object") return sendJson(res, 400, { error: "type_and_object_required" });
      const event = makeEvent(body.type, body.object, { livemode: Boolean(body.livemode), id: body.id });
      const delivery = await deliver(event, { signature: body.signature ?? "valid", url: body.url });
      return sendJson(res, 200, { event, delivery });
    }
    return sendJson(res, 404, { error: "not_found" });
  }

  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? "/", "http://fake.invalid");
      if (url.pathname.startsWith("/v1/")) return await handleApi(req, res, url);
      const pay = /^\/pay\/([^/]+)\/?$/.exec(url.pathname);
      if (pay && (req.method === "GET" || req.method === "POST")) return await handlePay(req, res, url, pay[1]);
      if (url.pathname.startsWith("/__control/")) return await handleControl(req, res, url);
      sendJson(res, 404, { error: "not_found" });
    } catch (err) {
      opts.log(`error: ${err?.stack ?? err}`);
      if (!res.headersSent) sendJson(res, 500, { error: { type: "api_error", message: String(err?.message ?? err) } });
      else res.end();
    }
  });
  server.keepAliveTimeout = 1000;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(opts.port, opts.host, resolve);
  });
  const { port } = server.address();
  baseUrl = `http://${opts.host}:${port}`;
  return {
    url: baseUrl,
    port,
    setWebhook({ webhookUrl, webhookSecret }) {
      if (webhookUrl !== undefined) opts.webhookUrl = webhookUrl;
      if (webhookSecret !== undefined) opts.webhookSecret = webhookSecret;
    },
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}

function cliArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const [k, inline] = a.slice(2).split(/=(.*)/s, 2);
    out[k] = inline !== undefined ? inline : argv[i + 1]?.startsWith("--") ? "true" : argv[++i];
  }
  return out;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const a = cliArgs(process.argv.slice(2));
  const fake = await startStripeFake({
    port: a.port ?? process.env.FAKE_STRIPE_PORT ?? 0,
    host: a.host ?? process.env.FAKE_STRIPE_HOST ?? "127.0.0.1",
    webhookUrl: a["webhook-url"] ?? process.env.FAKE_STRIPE_WEBHOOK_URL ?? "",
    webhookSecret: a["webhook-secret"] ?? process.env.FAKE_STRIPE_WEBHOOK_SECRET ?? "",
    deferMs: a["defer-ms"] ?? process.env.FAKE_STRIPE_DEFER_MS ?? 3000,
    log: (line) => console.log(`[stripe-fake] ${line}`),
  });
  console.log(`[stripe-fake] listening on ${fake.url}`);
  const stop = () => fake.close().then(() => process.exit(0));
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}
