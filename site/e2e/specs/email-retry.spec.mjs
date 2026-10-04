// A buyer email that fails on the first webhook delivery is sent on Stripe's redelivery of the same
// event, exactly once: the license is issued at once, the webhook answers 500 so Stripe retries, the
// sale notice is not repeated, and later redeliveries send nothing more.
import { test, expect } from "@playwright/test";
import {
  E,
  NOTIFY_TO,
  failNextEmails,
  fakeEvents,
  getOrder,
  listEmails,
  makeBuyer,
  payOnFakeCheckout,
  recipients,
  redeliver,
  startCheckout,
  waitForEmails,
  waitForOrderStatus,
} from "../support/e2e.mjs";

test("buyer email retried on redelivery, exactly once", async ({ page, request }) => {
  const buyer = makeBuyer("email-retry", test.info());
  const sessionId = await startCheckout(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });

  // Only emails to this buyer's address fail, so parallel tests keep their emails.
  await failNextEmails(request, { count: 1, status: 503, to: buyer.email });
  await payOnFakeCheckout(page, { name: buyer.name });

  // The fake still redirects; the license is issued even though the webhook answered 500.
  await page.waitForURL((u) => u.origin === new URL(E.base).origin && u.pathname === "/en/order/" && u.searchParams.get("session_id") === sessionId);
  await waitForOrderStatus(page, "issued");
  const licenseId = (await page.getByTestId("license-id").first().innerText()).trim();
  const firstDelivery = (await fakeEvents(request)).filter((e) => e.event.type === "checkout.session.completed" && e.event.data.object.id === sessionId);
  expect(firstDelivery.map((e) => e.delivery.status)).toEqual([500]);

  const buyerEmails = async () => (await listEmails(request)).filter((m) => recipients(m).includes(buyer.email) && String(m.subject).includes(licenseId));
  const saleNotices = async () => (await listEmails(request)).filter((m) => recipients(m).includes(NOTIFY_TO) && String(m.subject).includes(`License sale: ${licenseId}`));

  await waitForEmails(request, (m) => recipients(m).includes(NOTIFY_TO) && String(m.subject).includes(`License sale: ${licenseId}`));
  expect(await buyerEmails()).toHaveLength(0);

  // Stripe retries the same event: the buyer email goes out now, once.
  const retry = await redeliver(request, sessionId);
  expect(retry.event.id).toBe(firstDelivery[0].event.id);
  expect(retry.delivery.status, retry.delivery.body).toBe(200);
  await waitForEmails(request, (m) => recipients(m).includes(buyer.email) && String(m.subject).includes(licenseId));
  const sent = await buyerEmails();
  expect(sent).toHaveLength(1);
  expect(sent[0].subject).toBe(`[TEST] Your PineForge Codegen commercial license ${licenseId}`);
  expect((sent[0].attachments ?? []).map((a) => a.filename)).toEqual([`${licenseId}.json`]);

  // A further redelivery changes nothing.
  const again = await redeliver(request, sessionId);
  expect(again.delivery.status, again.delivery.body).toBe(200);
  await new Promise((r) => setTimeout(r, 1500));
  expect(await buyerEmails()).toHaveLength(1);
  expect(await saleNotices()).toHaveLength(1);

  const order = await getOrder(request, sessionId);
  expect(order.body.status).toBe("issued");
  expect(order.body.license.id).toBe(licenseId);
});
