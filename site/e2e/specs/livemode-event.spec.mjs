// 15. A livemode checkout.session.completed on the live-key deployment while live payments are blocked
// (draft agreement): the webhook answers 200, issues no license and alerts the operator. A live deployment
// ignores RESEND_API_BASE, so run.mjs gives it no Resend key (nothing may reach the real Resend): its alert
// is logged as skipped and is read from that deployment's email_log instead (inspection only).
import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import { E, NOTIFY_TO, getJson, isAlert, listEmails, makeBuyer, sendEvent } from "../support/e2e.mjs";

const npx = process.platform === "win32" ? "npx.cmd" : "npx";

/** `wrangler d1 execute` against the live-key deployment's local D1 (test setup and inspection only). */
function liveD1(command) {
  const r = spawnSync(npx, ["wrangler", "d1", "execute", "pineforge-license", "--local", "--persist-to", E.livePersistDir, "--json", "--command", command], {
    cwd: E.siteDir,
    encoding: "utf8",
    timeout: 120_000,
    env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "1" },
  });
  expect(r.status, `${r.stdout}\n${r.stderr}`).toBe(0);
  const out = JSON.parse(r.stdout.slice(r.stdout.indexOf("[")));
  return out.flatMap((x) => x.results ?? []);
}

const sql = (v) => (v === null ? "NULL" : typeof v === "number" ? String(v) : `'${String(v).replace(/'/g, "''")}'`);

test("a livemode completed event on the live-key deployment issues nothing and alerts", async ({ request }) => {
  const buyer = makeBuyer("livemode", test.info());
  const orderId = `ord_e2e${crypto.randomBytes(8).toString("hex")}`;
  const sessionId = `cs_live_e2e${crypto.randomBytes(12).toString("hex")}`;
  const amount = 240000;
  const now = new Date().toISOString();

  // TEST SETUP ONLY: checkout on this deployment answers 503 (live payments disabled), so no order can be
  // created through its API. Seed one pending order row straight into ITS local D1, as checkout would.
  const row = {
    id: orderId,
    status: "pending",
    company: buyer.company,
    country: buyer.country.code,
    buyer_name: buyer.name,
    buyer_email: buyer.email,
    reference: buyer.reference,
    tier: "team",
    option_id: "seats-5",
    term_months: 12,
    product_name: "PineForge Codegen commercial license — Team, 5 seats, 12 months",
    scope_summary: "5 seats",
    scope_json: JSON.stringify({ seats: 5 }),
    amount_subtotal: amount,
    currency: "usd",
    locale: "en",
    stripe_session_id: sessionId,
    livemode: 1,
    agreement_version: "e2e-seed",
    agreement_sha256: "0".repeat(64),
    created_at: now,
    updated_at: now,
  };
  liveD1(`INSERT INTO orders (${Object.keys(row).join(", ")}) VALUES (${Object.values(row).map(sql).join(", ")})`);

  const paymentIntent = `pi_live_e2e${crypto.randomBytes(8).toString("hex")}`;
  const session = {
    id: sessionId,
    object: "checkout.session",
    amount_subtotal: amount,
    amount_total: amount,
    client_reference_id: orderId,
    currency: "usd",
    customer: `cus_live_e2e${crypto.randomBytes(4).toString("hex")}`,
    customer_details: { email: buyer.email, name: buyer.name, address: { country: "DE" } },
    invoice: null,
    livemode: true,
    metadata: { order_id: orderId, tier: "team", option: "seats-5", term: "12" },
    mode: "payment",
    payment_intent: paymentIntent,
    payment_status: "paid",
    status: "complete",
  };
  const sent = await sendEvent(request, { type: "checkout.session.completed", object: session, livemode: true, url: `${E.live}/api/stripe/webhook` });
  expect(sent.event.livemode).toBe(true);
  expect(sent.delivery.status, sent.delivery.body).toBe(200);

  const order = await getJson(request, `${E.live}/api/order?session_id=${encodeURIComponent(sessionId)}`);
  expect(order.status, order.text).toBe(200);
  expect(order.body.license ?? null).toBeNull();
  expect(order.body.status).not.toBe("issued");

  // The operator alert for this order (inspection of the deployment's own email log, see the header).
  const alerts = liveD1(`SELECT to_addr, subject, kind, related_id, status FROM email_log WHERE related_id = ${sql(orderId)}`).filter((r) => isAlert(r));
  expect(alerts, JSON.stringify(alerts)).toHaveLength(1);
  expect(alerts[0].to_addr).toContain(NOTIFY_TO);
  // Nothing to the buyer: no license exists.
  expect((await listEmails(request)).filter((m) => JSON.stringify(m.to).toLowerCase().includes(buyer.email))).toEqual([]);
});
