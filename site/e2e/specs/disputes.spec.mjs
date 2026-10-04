// 13. Disputes: a signed charge.dispute.created / charge.dispute.closed from the Stripe fake. A lost dispute
// revokes the license (verify, certificate REVOKED) and alerts the operator; a won one leaves it valid; a
// re-sent "created" for the same dispute alerts once. A lost dispute on an order that has no license marks the
// order disputed, so a later delivery of its completed event issues nothing.
import { test, expect } from "@playwright/test";
import crypto from "node:crypto";
import {
  E,
  apiCheckout,
  dispute,
  fakeSession,
  getJson,
  isAlert,
  listEmails,
  makeBuyer,
  paidSessionObject,
  payOverHttp,
  sendEvent,
  waitForEmails,
  waitForOrderApi,
} from "../support/e2e.mjs";

/** A paid, issued order over HTTP; returns its ids. */
async function paidOrder(request, label) {
  const buyer = makeBuyer(label, test.info());
  const { sessionId } = await apiCheckout(request, buyer);
  await payOverHttp(request, sessionId, { email: buyer.email });
  const order = await waitForOrderApi(request, sessionId, "issued");
  const { session, charge } = await fakeSession(request, sessionId);
  expect(session.payment_intent).toMatch(/^pi_/);
  return { buyer, sessionId, licenseId: order.license.id, paymentIntent: session.payment_intent, chargeId: charge.id, orderId: order.order?.id };
}

/** Operator alerts that name this dispute, its charge, payment or license. */
const alertsFor = (ids) => (m) => isAlert(m) && ids.some((id) => id && JSON.stringify(m).includes(id));

test("a lost dispute revokes the license and alerts the operator", async ({ page, request }) => {
  const o = await paidOrder(request, "dispute-lost");
  const created = await dispute(request, o.paymentIntent, "created");
  expect(created.event.type).toBe("charge.dispute.created");
  expect(created.dispute).toMatchObject({ object: "dispute", charge: o.chargeId, payment_intent: o.paymentIntent, status: "needs_response" });
  expect(created.dispute.id).toMatch(/^dp_/);
  expect(created.delivery.status, created.delivery.body).toBe(200);

  const closed = await dispute(request, o.paymentIntent, "closed", "lost");
  expect(closed.event.type).toBe("charge.dispute.closed");
  expect(closed.dispute).toMatchObject({ id: created.dispute.id, status: "lost" });
  expect(closed.delivery.status, closed.delivery.body).toBe(200);

  const api = await getJson(request, `${E.base}/api/verify?id=${o.licenseId}`);
  expect(api.body).toMatchObject({ valid: false, status: "revoked" });
  expect(api.body.license.revokedAt).toBeTruthy();

  await page.goto(`/certificate/${o.licenseId}`);
  await expect(page.getByTestId("certificate")).toHaveAttribute("data-status", "revoked");
  await expect(page.getByTestId("revoked-banner")).toContainText("REVOKED");

  // The verify page states the reason in words; the stored reason code stays off the page.
  await page.goto(`/en/verify/?id=${o.licenseId}`);
  await expect(page.getByTestId("verify-result")).toHaveAttribute("data-status", "revoked");
  await expect(page.locator("body")).not.toContainText("dispute_lost", { useInnerText: true });

  await waitForEmails(request, alertsFor([created.dispute.id, o.chargeId, o.paymentIntent, o.licenseId, o.orderId]));
  // The lost alert itself, not only the one for the new dispute: its subject names the dispute.
  const lostAlerts = await waitForEmails(request, (m) => isAlert(m) && String(m.subject).includes(created.dispute.id) && /lost/i.test(String(m.subject)));
  expect(lostAlerts[0].subject).toMatch(/^ALERT:/);
  expect(lostAlerts[0].subject).toContain(created.dispute.id);
});

test("a won dispute leaves the license valid", async ({ request }) => {
  const o = await paidOrder(request, "dispute-won");
  const created = await dispute(request, o.paymentIntent, "created");
  expect(created.delivery.status, created.delivery.body).toBe(200);
  const closed = await dispute(request, o.paymentIntent, "closed", "won");
  expect(closed.dispute).toMatchObject({ id: created.dispute.id, status: "won" });
  expect(closed.delivery.status, closed.delivery.body).toBe(200);

  const api = await getJson(request, `${E.base}/api/verify?id=${o.licenseId}`);
  expect(api.body).toMatchObject({ valid: true, status: "valid" });
});

test("the same dispute created twice alerts once", async ({ request }) => {
  const o = await paidOrder(request, "dispute-twice");
  const first = await dispute(request, o.paymentIntent, "created");
  expect(first.delivery.status, first.delivery.body).toBe(200);
  const second = await dispute(request, o.paymentIntent, "created");
  expect(second.delivery.status, second.delivery.body).toBe(200);
  expect(second.dispute.id).toBe(first.dispute.id);
  expect(second.event.id).not.toBe(first.event.id);

  const match = alertsFor([first.dispute.id, o.chargeId, o.paymentIntent, o.licenseId, o.orderId]);
  await waitForEmails(request, match);
  // Each delivery answered after its handling; give a late second alert time to land anyway.
  await new Promise((r) => setTimeout(r, 2000));
  expect((await listEmails(request)).filter(match)).toHaveLength(1);
});

// On the orphan instance (run.mjs) issuance always fails, so the paid order keeps no license: its completed event
// answers 500. A lost dispute on that payment marks the order disputed; Stripe's retry of the same completed event
// then answers 200 and issues nothing.
test("a lost dispute on an order without a license, then a redelivered completed event, issues nothing", async ({ request }) => {
  const buyer = makeBuyer("dispute-unissued", test.info());
  const { sessionId } = await apiCheckout(request, buyer, {}, E.orphan);
  const { session } = await fakeSession(request, sessionId);
  const orderId = session.client_reference_id;
  expect(orderId).toMatch(/^ord_/);
  const url = `${E.orphan}/api/stripe/webhook`;
  const orphanOrder = () => getJson(request, `${E.orphan}/api/order?session_id=${encodeURIComponent(sessionId)}`);

  // The fake hosted page would deliver to the main instance's webhook; post the paid event to this one.
  const paid = paidSessionObject(session);
  const eventId = `evt_e2e${crypto.randomBytes(12).toString("hex")}`;
  const first = await sendEvent(request, { type: "checkout.session.completed", object: paid, id: eventId, url });
  expect(first.delivery.status, first.delivery.body).toBe(500);
  const before = await orphanOrder();
  expect(before.status, before.text).toBe(200);
  expect(before.body.license ?? null).toBeNull();

  // The /__control/dispute shortcut needs a charge the fake made; this payment is forged, so the dispute is built
  // here, for the payment the order recorded.
  const disputeId = `dp_e2e${crypto.randomBytes(10).toString("hex")}`;
  const lost = await sendEvent(request, {
    type: "charge.dispute.closed",
    object: {
      id: disputeId,
      object: "dispute",
      amount: paid.amount_total ?? paid.amount_subtotal,
      balance_transactions: [],
      charge: `ch_e2e${crypto.randomBytes(10).toString("hex")}`,
      created: Math.floor(Date.now() / 1000),
      currency: paid.currency ?? "usd",
      is_charge_refundable: false,
      livemode: false,
      metadata: {},
      payment_intent: paid.payment_intent,
      reason: "fraudulent",
      status: "lost",
    },
    url,
  });
  expect(lost.event.type).toBe("charge.dispute.closed");
  expect(lost.delivery.status, lost.delivery.body).toBe(200);
  const lostAlerts = await waitForEmails(request, (m) => isAlert(m) && String(m.subject).includes(disputeId));
  expect(lostAlerts[0].subject).toMatch(/^ALERT:/);

  // Stripe's retry of the same completed event: accepted, and no issuance is attempted for a disputed order.
  const retry = await sendEvent(request, { type: "checkout.session.completed", object: paid, id: eventId, url });
  expect(retry.event.id).toBe(eventId);
  expect(retry.delivery.status, retry.delivery.body).toBe(200);

  const after = await orphanOrder();
  expect(after.status, after.text).toBe(200);
  expect(after.body.license ?? null).toBeNull();
  expect(after.body.status).not.toBe("issued");
  // A paid order without a license reads "pending"; a disputed one never does again.
  expect(after.body.status).not.toBe("pending");
  // Nothing went to the buyer: there is no license to send.
  expect((await listEmails(request)).filter((m) => JSON.stringify(m.to).toLowerCase().includes(buyer.email))).toEqual([]);
});
