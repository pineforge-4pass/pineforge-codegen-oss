#!/usr/bin/env node
// Self-test of the two E2E fakes, independent of the site: drives the Stripe fake with the official
// `stripe` npm client, checks that its webhook events verify with `stripe.webhooks.constructEventAsync`,
// and exercises the Resend sink. Run: node e2e/fakes/selftest.mjs   (exit 0 = every check passed)
import http from "node:http";
import crypto from "node:crypto";
import assert from "node:assert/strict";
import Stripe from "stripe";
import { startStripeFake } from "./stripe.mjs";
import { startResendFake } from "./resend.mjs";

const secret = `whsec_${crypto.randomBytes(24).toString("hex")}`;
const received = [];
const receiverStripe = new Stripe("sk_test_receiver", { httpClient: Stripe.createFetchHttpClient() });
const receiver = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString("utf8");
  try {
    const event = await receiverStripe.webhooks.constructEventAsync(raw, req.headers["stripe-signature"] ?? "", secret, undefined, Stripe.createSubtleCryptoProvider());
    received.push({ ok: true, event });
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ received: true }));
  } catch (err) {
    received.push({ ok: false, error: String(err?.message ?? err) });
    res.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "invalid_signature" }));
  }
});
await new Promise((r) => receiver.listen(0, "127.0.0.1", r));
const webhookUrl = `http://127.0.0.1:${receiver.address().port}/api/stripe/webhook`;

const fake = await startStripeFake({ webhookUrl, webhookSecret: secret });
const sink = await startResendFake();
const base = new URL(fake.url);
const stripe = new Stripe("sk_test_e2e", {
  httpClient: Stripe.createFetchHttpClient(),
  host: base.hostname,
  port: Number(base.port),
  protocol: "http",
  maxNetworkRetries: 0,
});

let failures = 0;
async function check(name, fn) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failures += 1;
    console.log(`FAIL ${name}\n     ${String(err?.stack ?? err).split("\n").slice(0, 6).join("\n     ")}`);
  }
}

const orderId = `ord_selftest_${Date.now()}`;
const createParams = {
  mode: "payment",
  line_items: [
    {
      quantity: 1,
      price_data: {
        currency: "usd",
        unit_amount: 240000,
        product_data: { name: "PineForge Codegen commercial license — Team, 5 seats, 12 months" },
      },
    },
  ],
  customer_email: "buyer@example.com",
  client_reference_id: orderId,
  metadata: { order_id: orderId, tier: "team", option: "seats-5", term: "12" },
  invoice_creation: { enabled: true, invoice_data: { description: "PineForge Codegen", metadata: { order_id: orderId } } },
  automatic_tax: { enabled: false },
  tax_id_collection: { enabled: true },
  billing_address_collection: "required",
  success_url: "http://127.0.0.1:1/en/order/?session_id={CHECKOUT_SESSION_ID}",
  cancel_url: "http://127.0.0.1:1/en/checkout/?tier=team&option=seats-5",
};

let session;
await check("create a Checkout Session with the official client", async () => {
  session = await stripe.checkout.sessions.create(createParams, { idempotencyKey: orderId });
  assert.match(session.id, /^cs_test_/);
  assert.equal(session.object, "checkout.session");
  assert.equal(session.status, "open");
  assert.equal(session.payment_status, "unpaid");
  assert.equal(session.amount_subtotal, 240000);
  assert.equal(session.amount_total, 240000);
  assert.equal(session.currency, "usd");
  assert.equal(session.client_reference_id, orderId);
  assert.equal(session.metadata.order_id, orderId);
  assert.equal(session.customer_email, "buyer@example.com");
  assert.equal(session.livemode, false);
  assert.equal(session.payment_intent, null);
  assert.equal(session.url, `${fake.url}/pay/${session.id}`);
});

await check("same Idempotency-Key returns the same session", async () => {
  const again = await stripe.checkout.sessions.create(createParams, { idempotencyKey: orderId });
  assert.equal(again.id, session.id);
  const { sessions } = await (await fetch(`${fake.url}/__control/sessions`)).json();
  assert.equal(sessions.length, 1);
});

await check("recorded params keep the bracket structure", async () => {
  const rec = await (await fetch(`${fake.url}/__control/sessions/${session.id}`)).json();
  assert.equal(rec.idempotencyKey, orderId);
  assert.equal(rec.params.line_items[0].price_data.unit_amount, "240000");
  assert.equal(rec.params.line_items[0].price_data.product_data.name, createParams.line_items[0].price_data.product_data.name);
  assert.equal(rec.params.invoice_creation.enabled, "true");
  assert.equal(rec.params.invoice_creation.invoice_data.metadata.order_id, orderId);
  assert.equal(rec.params.tax_id_collection.enabled, "true");
  assert.equal(rec.params.automatic_tax.enabled, "false");
  assert.equal(rec.params.billing_address_collection, "required");
});

await check("retrieve, with and without expand[]=line_items", async () => {
  const s = await stripe.checkout.sessions.retrieve(session.id);
  assert.equal(s.id, session.id);
  const e = await stripe.checkout.sessions.retrieve(session.id, { expand: ["line_items"] });
  assert.equal(e.line_items.data[0].amount_subtotal, 240000);
  const items = await stripe.checkout.sessions.listLineItems(session.id);
  assert.equal(items.data[0].quantity, 1);
});

await check("a live or missing key is refused (401)", async () => {
  const live = new Stripe("sk_live_x", { httpClient: Stripe.createFetchHttpClient(), host: base.hostname, port: Number(base.port), protocol: "http", maxNetworkRetries: 0 });
  await assert.rejects(() => live.checkout.sessions.create(createParams), (err) => err.statusCode === 401);
  const r = await fetch(`${fake.url}/v1/checkout/sessions`, { method: "POST", body: "mode=payment" });
  assert.equal(r.status, 401);
  const { requests } = await (await fetch(`${fake.url}/__control/requests`)).json();
  assert.ok(requests.some((q) => q.auth.startsWith("sk_live_") && q.status === 401));
});

const payForm = (card) =>
  new URLSearchParams({ email: "buyer@example.com", cardNumber: card, expiry: "12 / 34", cvc: "123", name: "E2E Buyer", country: "US" }).toString();

await check("hosted page renders the labelled fields", async () => {
  const html = await (await fetch(session.url)).text();
  for (const label of ["Email", "Card number", "Expiration (MM / YY)", "CVC", "Cardholder name", "Country or region"]) {
    assert.ok(html.includes(`>${label}</label>`), `label ${label}`);
  }
  assert.ok(html.includes(">Pay</button>"));
  assert.ok(html.includes("$2,400.00"));
});

await check("card 4000 0000 0000 0002 is declined, no event", async () => {
  const before = received.length;
  const r = await fetch(session.url, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: payForm("4000 0000 0000 0002"), redirect: "manual" });
  assert.equal(r.status, 402);
  assert.ok((await r.text()).includes("Your card was declined."));
  assert.equal(received.length, before);
  assert.equal((await stripe.checkout.sessions.retrieve(session.id)).status, "open");
});

let paid;
await check("card 4242 pays, posts a verifiable checkout.session.completed, then 303 to success_url", async () => {
  const r = await fetch(session.url, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: payForm("4242 4242 4242 4242"), redirect: "manual" });
  assert.equal(r.status, 303);
  assert.equal(r.headers.get("location"), `http://127.0.0.1:1/en/order/?session_id=${session.id}`);
  const last = received.at(-1);
  assert.ok(last?.ok, last?.error);
  assert.equal(last.event.type, "checkout.session.completed");
  assert.equal(last.event.object, "event");
  assert.equal(last.event.livemode, false);
  assert.match(last.event.id, /^evt_/);
  const obj = last.event.data.object;
  assert.equal(obj.id, session.id);
  assert.equal(obj.payment_status, "paid");
  assert.equal(obj.status, "complete");
  assert.match(obj.payment_intent, /^pi_/);
  assert.match(obj.customer, /^cus_/);
  assert.match(obj.invoice, /^in_/);
  paid = await stripe.checkout.sessions.retrieve(session.id);
  assert.equal(paid.payment_status, "paid");
  const { events } = await (await fetch(`${fake.url}/__control/events`)).json();
  assert.equal(events.at(-1).delivery.status, 200);
});

await check("control refund posts a verifiable charge.refunded", async () => {
  const r = await fetch(`${fake.url}/__control/refund`, { method: "POST", body: JSON.stringify({ payment_intent: paid.payment_intent, amount: 1000 }) });
  const partial = await r.json();
  assert.equal(partial.delivery.status, 200);
  assert.equal(partial.charge.refunded, false);
  const full = await (await fetch(`${fake.url}/__control/refund`, { method: "POST", body: JSON.stringify({ payment_intent: paid.payment_intent }) })).json();
  assert.equal(full.delivery.status, 200);
  const ev = received.at(-1);
  assert.ok(ev.ok, ev.error);
  assert.equal(ev.event.type, "charge.refunded");
  const ch = ev.event.data.object;
  assert.equal(ch.object, "charge");
  assert.equal(ch.amount, 240000);
  assert.equal(ch.amount_refunded, 240000);
  assert.equal(ch.refunded, true);
  assert.equal(ch.payment_intent, paid.payment_intent);
  assert.match(ch.id, /^ch_/);
});

for (const signature of ["bad", "none", "stale", "wrong-secret"]) {
  await check(`send-event with signature=${signature} fails verification`, async () => {
    const r = await (await fetch(`${fake.url}/__control/send-event`, { method: "POST", body: JSON.stringify({ type: "checkout.session.completed", object: paid, signature }) })).json();
    assert.equal(r.delivery.status, 400);
    assert.equal(received.at(-1).ok, false);
  });
}

await check("send-event with a valid signature verifies", async () => {
  const r = await (await fetch(`${fake.url}/__control/send-event`, { method: "POST", body: JSON.stringify({ type: "checkout.session.expired", object: paid }) })).json();
  assert.equal(r.delivery.status, 200);
  assert.equal(received.at(-1).event.type, "checkout.session.expired");
});

await check("resend sink: Bearer required, stores and lists", async () => {
  const no = await fetch(`${sink.url}/emails`, { method: "POST", body: JSON.stringify({ from: "a@example.com", to: ["b@example.com"], subject: "s", text: "t" }) });
  assert.equal(no.status, 401);
  const ok = await fetch(`${sink.url}/emails`, {
    method: "POST",
    headers: { Authorization: "Bearer re_test_e2e", "Content-Type": "application/json" },
    body: JSON.stringify({ from: "a@example.com", to: ["b@example.com"], subject: "s", text: "t", reply_to: "c@example.com", attachments: [{ filename: "x.json", content: Buffer.from("{}").toString("base64") }] }),
  });
  assert.equal(ok.status, 200);
  const { id } = await ok.json();
  assert.ok(id);
  const { emails } = await (await fetch(`${sink.url}/__control/emails?to=b@example.com`)).json();
  assert.equal(emails.length, 1);
  assert.equal(emails[0].id, id);
  assert.equal(emails[0].attachments[0].filename, "x.json");
});

await fake.close();
await sink.close();
receiver.close();
console.log(failures ? `\n${failures} check(s) failed` : "\nall fake self-tests passed");
process.exit(failures ? 1 : 0);
