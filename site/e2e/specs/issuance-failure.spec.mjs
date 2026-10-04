// 16. Issuance failure: on the third instance (run.mjs) LICENSE_SIGNING_KEY's kid is not in its keyring, so the
// signed license fails the self-check. A paid order's webhook answers 500 (Stripe retries), no license exists,
// and the operator gets exactly ONE "ALERT:" issuance email for the order across a redelivery.
import { test, expect } from "@playwright/test";
import crypto from "node:crypto";
import { E, apiCheckout, fakeSession, getJson, isAlert, listEmails, makeBuyer, paidSessionObject, sendEvent, waitForEmails } from "../support/e2e.mjs";

test("a signing key outside the keyring fails issuance with 500 and one alert per order", async ({ request }) => {
  const buyer = makeBuyer("orphan-kid", test.info());
  const { sessionId } = await apiCheckout(request, buyer, {}, E.orphan);
  const { session } = await fakeSession(request, sessionId);
  const orderId = session.client_reference_id;
  expect(orderId).toMatch(/^ord_/);

  // The fake hosted page would deliver to the main instance's webhook; post the paid event to this one.
  const paid = paidSessionObject(session);
  const eventId = `evt_e2e${crypto.randomBytes(12).toString("hex")}`;
  const url = `${E.orphan}/api/stripe/webhook`;
  const first = await sendEvent(request, { type: "checkout.session.completed", object: paid, id: eventId, url });
  expect(first.delivery.status, first.delivery.body).toBe(500);
  // Stripe's retry of the same event.
  const retry = await sendEvent(request, { type: "checkout.session.completed", object: paid, id: eventId, url });
  expect(retry.event.id).toBe(eventId);
  expect(retry.delivery.status, retry.delivery.body).toBe(500);

  const order = await getJson(request, `${E.orphan}/api/order?session_id=${encodeURIComponent(sessionId)}`);
  expect(order.status, order.text).toBe(200);
  expect(order.body.license ?? null).toBeNull();

  const match = (m) => isAlert(m) && JSON.stringify(m).includes(orderId);
  await waitForEmails(request, match);
  await new Promise((r) => setTimeout(r, 2000));
  const alerts = (await listEmails(request)).filter(match);
  expect(alerts, JSON.stringify(alerts.map((m) => m.subject))).toHaveLength(1);
  expect(alerts[0].subject).toMatch(/issu/i);
  // The buyer got nothing: there is no license to send.
  expect((await listEmails(request)).filter((m) => JSON.stringify(m.to).toLowerCase().includes(buyer.email))).toEqual([]);
});
