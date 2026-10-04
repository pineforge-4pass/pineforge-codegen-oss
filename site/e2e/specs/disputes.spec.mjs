// 13. Disputes: a signed charge.dispute.created / charge.dispute.closed from the Stripe fake. A lost dispute
// revokes the license (verify, certificate REVOKED) and alerts the operator; a won one leaves it valid; a
// re-sent "created" for the same dispute alerts once.
import { test, expect } from "@playwright/test";
import { E, apiCheckout, dispute, fakeSession, getJson, isAlert, listEmails, makeBuyer, payOverHttp, waitForEmails, waitForOrderApi } from "../support/e2e.mjs";

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

  await waitForEmails(request, alertsFor([created.dispute.id, o.chargeId, o.paymentIntent, o.licenseId, o.orderId]));
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
