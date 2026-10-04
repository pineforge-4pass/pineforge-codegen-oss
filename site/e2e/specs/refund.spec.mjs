// 5. Refund: a partial refund only records; a full refund through the fake (charge.refunded) revokes the
// license: verify says revoked, the certificate shows REVOKED, the order page says refunded.
import { test, expect } from "@playwright/test";
import { E, apiCheckout, fakeEvents, fakeSession, getJson, getOrder, makeBuyer, payOverHttp, purchase, refund, sendEvent, waitForOrderApi, waitForOrderStatus } from "../support/e2e.mjs";

test("full refund revokes the license", async ({ page, request }) => {
  const buyer = makeBuyer("refund", test.info());
  const { sessionId, licenseId } = await purchase(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });
  const { session } = await fakeSession(request, sessionId);
  expect(session.payment_intent).toMatch(/^pi_/);

  // Partial refund: recorded only, the license stays valid.
  const partial = await refund(request, session.payment_intent, 10000);
  expect(partial.delivery.status, partial.delivery.body).toBe(200);
  expect(partial.charge.refunded).toBe(false);
  expect((await getJson(request, `${E.base}/api/verify?id=${licenseId}`)).body).toMatchObject({ valid: true, status: "valid" });
  expect((await getOrder(request, sessionId)).body.status).toBe("issued");

  // Full refund of the rest.
  const full = await refund(request, session.payment_intent);
  expect(full.delivery.status, full.delivery.body).toBe(200);
  expect(full.charge.refunded).toBe(true);
  expect(full.charge.amount_refunded).toBe(240000);

  // A redelivery of the same event is accepted and changes nothing.
  const again = await sendEvent(request, { type: "charge.refunded", object: full.event.data.object, id: full.event.id });
  expect(again.delivery.status, again.delivery.body).toBe(200);

  await page.goto(`/en/verify/?id=${licenseId}`);
  const result = page.getByTestId("verify-result");
  await expect(result).toHaveAttribute("data-status", "revoked");
  await expect(result).toHaveAttribute("data-valid", "false");
  const api = await getJson(request, `${E.base}/api/verify?id=${licenseId}`);
  expect(api.body).toMatchObject({ valid: false, status: "revoked" });
  expect(api.body.license.revokedAt).toBeTruthy();
  expect(api.body.license.revokeReason).toBe("refund");

  await page.goto(`/certificate/${licenseId}`);
  await expect(page.getByTestId("certificate")).toHaveAttribute("data-status", "revoked");
  const banner = page.getByTestId("revoked-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("REVOKED");

  await page.goto(`/en/order/?session_id=${sessionId}`);
  await waitForOrderStatus(page, "refunded", 15_000);
  const order = await getOrder(request, sessionId);
  expect(order.body.status).toBe("refunded");
  expect(order.body.license.status).toBe("revoked");
});

// The refund lands before the payment is recorded: card 4000 0000 0000 0077 delivers checkout.session.completed
// a few seconds after the redirect, and the refund is sent at once. The Charge carries the PaymentIntent's
// metadata (order_id), as on Stripe; the completed event that arrives afterwards must not issue a license.
test("a refund before the payment is recorded leaves no active license", async ({ request }) => {
  const buyer = makeBuyer("refund-early", test.info());
  const { sessionId } = await apiCheckout(request, buyer);
  await payOverHttp(request, sessionId, { card: "4000 0000 0000 0077", email: buyer.email });
  const { session, charge } = await fakeSession(request, sessionId);
  expect(session.payment_intent).toMatch(/^pi_/);
  expect(charge.metadata.order_id).toMatch(/^ord_/);

  const full = await refund(request, session.payment_intent);
  expect(full.charge.refunded).toBe(true);
  expect(full.delivery.status, full.delivery.body).toBe(200);
  const early = (await fakeEvents(request)).find((r) => r.event.type === "checkout.session.completed" && r.event.data.object.id === sessionId);
  expect(early, "the completed event must still be pending when the refund lands").toBeFalsy();

  // Now the deferred completed event arrives.
  let completed;
  await expect
    .poll(
      async () => {
        completed = (await fakeEvents(request)).find((r) => r.event.type === "checkout.session.completed" && r.event.data.object.id === sessionId);
        return completed?.delivery.status ?? 0;
      },
      { timeout: 30_000 },
    )
    .not.toBe(0);
  expect(completed.delivery.status, completed.delivery.body).toBe(200);

  const order = await waitForOrderApi(request, sessionId, "refunded", 15_000);
  if (order.license) {
    expect(order.license.status).not.toBe("active");
    const api = await getJson(request, `${E.base}/api/verify?id=${order.license.id}`);
    expect(api.body.valid).toBe(false);
  }
});
