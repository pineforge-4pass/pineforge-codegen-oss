// Webhook event handling with correctly signed events from the Stripe fake: delayed (async) payments,
// expired and failed sessions, redeliveries, and an event whose client_reference_id belongs to another
// order. At most one license per order, and only for a paid session that matches its order.
import { test, expect } from "@playwright/test";
import { apiCheckout, fakeSession, getOrder, listEmails, makeBuyer, paidSessionObject, sendEvent, waitForEmails } from "../support/e2e.mjs";

const orderStatus = async (request, sessionId) => (await getOrder(request, sessionId)).body.status;

test("async payment: completed but unpaid issues nothing; async_payment_succeeded issues once", async ({ request }) => {
  const buyer = makeBuyer("async", test.info());
  const { sessionId } = await apiCheckout(request, buyer);
  const session = (await fakeSession(request, sessionId)).session;

  const unpaid = { ...paidSessionObject(session), payment_status: "unpaid" };
  const first = await sendEvent(request, { type: "checkout.session.completed", object: unpaid });
  expect(first.delivery.status, first.delivery.body).toBe(200);
  const pending = await getOrder(request, sessionId);
  expect(pending.body.status).toBe("pending");
  expect(pending.body.license).toBeNull();

  const paid = { ...unpaid, payment_status: "paid" };
  const ok = await sendEvent(request, { type: "checkout.session.async_payment_succeeded", object: paid });
  expect(ok.delivery.status, ok.delivery.body).toBe(200);
  await expect.poll(() => orderStatus(request, sessionId), { timeout: 15_000 }).toBe("issued");
  const issued = await getOrder(request, sessionId);
  const licenseId = issued.body.license.id;
  expect(issued.body.signedLicense.license.id).toBe(licenseId);

  // The same event delivered again, and another paid event for the same session: still one license.
  const again = await sendEvent(request, { type: "checkout.session.async_payment_succeeded", object: paid, id: ok.event.id });
  expect(again.delivery.status, again.delivery.body).toBe(200);
  const other = await sendEvent(request, { type: "checkout.session.completed", object: paid });
  expect(other.delivery.status, other.delivery.body).toBe(200);
  const after = await getOrder(request, sessionId);
  expect(after.body.status).toBe("issued");
  expect(after.body.license.id).toBe(licenseId);
  await waitForEmails(request, (m) => String(m.subject).includes(licenseId), { min: 2 });
  await new Promise((r) => setTimeout(r, 1500));
  expect((await listEmails(request)).filter((m) => String(m.subject).includes(licenseId))).toHaveLength(2);
});

test("expired and async-failed sessions mark their orders and issue nothing", async ({ request }) => {
  const expiredBuyer = makeBuyer("expired", test.info());
  const expired = await apiCheckout(request, expiredBuyer);
  const expiredSession = (await fakeSession(request, expired.sessionId)).session;
  const e = await sendEvent(request, { type: "checkout.session.expired", object: { ...expiredSession, status: "expired" } });
  expect(e.delivery.status, e.delivery.body).toBe(200);
  await expect.poll(() => orderStatus(request, expired.sessionId), { timeout: 15_000 }).toBe("expired");
  expect((await getOrder(request, expired.sessionId)).body.license).toBeNull();

  const failedBuyer = makeBuyer("async-failed", test.info());
  const failed = await apiCheckout(request, failedBuyer);
  const failedSession = (await fakeSession(request, failed.sessionId)).session;
  const f = await sendEvent(request, {
    type: "checkout.session.async_payment_failed",
    object: { ...paidSessionObject(failedSession), payment_status: "unpaid" },
  });
  expect(f.delivery.status, f.delivery.body).toBe(200);
  await expect.poll(() => orderStatus(request, failed.sessionId), { timeout: 15_000 }).toBe("failed");
  expect((await getOrder(request, failed.sessionId)).body.license).toBeNull();
});

test("an event whose client_reference_id names another order issues nothing", async ({ request }) => {
  const a = await apiCheckout(request, makeBuyer("crossed-a", test.info()));
  const b = await apiCheckout(request, makeBuyer("crossed-b", test.info()));
  const sessionA = (await fakeSession(request, a.sessionId)).session;
  const sessionB = (await fakeSession(request, b.sessionId)).session;
  expect(sessionA.client_reference_id).not.toBe(sessionB.client_reference_id);

  const crossed = paidSessionObject(sessionA, { client_reference_id: sessionB.client_reference_id });
  const r = await sendEvent(request, { type: "checkout.session.completed", object: crossed });
  expect(r.delivery.status).toBeLessThan(500);
  await new Promise((res) => setTimeout(res, 1000));
  for (const id of [a.sessionId, b.sessionId]) {
    const order = await getOrder(request, id);
    expect(order.body.status).not.toBe("issued");
    expect(order.body.license).toBeNull();
  }
});
