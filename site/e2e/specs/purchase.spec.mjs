// 2. Purchase: plans -> Team 5 seats -> checkout -> fake hosted checkout -> 4242 -> order page with the
// issued license -> certificate (active) -> verify (valid). A declined card issues nothing.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import {
  E,
  LICENSE_ID_RE,
  fakeEvents,
  fakeSession,
  getJson,
  getOrder,
  makeBuyer,
  payOnFakeCheckout,
  startCheckout,
  waitForOrderStatus,
} from "../support/e2e.mjs";

test("Team 5 seats: pay with 4242, license issued, certificate active, verify valid", async ({ page, request }) => {
  const buyer = makeBuyer("purchase", test.info());
  const sessionId = await startCheckout(page, { tier: "team", option: "seats-5", buyer });

  // What the site asked Stripe for (recorded by the fake).
  const rec = await fakeSession(request, sessionId);
  const p = rec.params;
  expect(p.mode).toBe("payment");
  expect(p.customer_email).toBe(buyer.email);
  expect(p.line_items).toHaveLength(1);
  expect(p.line_items[0].quantity).toBe("1");
  expect(p.line_items[0].price_data.currency).toBe("usd");
  expect(p.line_items[0].price_data.unit_amount).toBe("240000");
  expect(p.line_items[0].price_data.product_data.name).toBe("PineForge Codegen commercial license — Team, 5 seats, 12 months");
  expect(p.invoice_creation.enabled).toBe("true");
  expect(p.tax_id_collection.enabled).toBe("true");
  expect(p.billing_address_collection).toBe("required");
  expect(p.automatic_tax.enabled).toBe("false");
  expect(p.client_reference_id).toMatch(/\S/);
  expect(p.metadata.order_id).toBe(p.client_reference_id);
  expect(p.metadata.tier).toBe("team");
  expect(p.metadata.option).toBe("seats-5");
  expect(rec.idempotencyKey).toContain(p.client_reference_id);
  expect(p.success_url).toBe(`${E.base}/en/order/?session_id={CHECKOUT_SESSION_ID}`);
  expect(p.cancel_url).toContain(`${E.base}/en/checkout/`);
  expect(rec.session.amount_subtotal).toBe(240000);
  expect(rec.session.currency).toBe("usd");

  // The fake hosted checkout shows the order the site created.
  await expect(page.getByTestId("fake-total")).toHaveText("$2,400.00");
  await expect(page.getByLabel("Email", { exact: true })).toHaveValue(buyer.email);
  await payOnFakeCheckout(page, { name: buyer.name });
  await page.waitForURL((u) => u.origin === new URL(E.base).origin && u.pathname === "/en/order/" && u.searchParams.get("session_id") === sessionId);
  await waitForOrderStatus(page, "issued");
  const licenseId = (await page.getByTestId("license-id").first().innerText()).trim();
  expect(licenseId).toMatch(LICENSE_ID_RE);

  // The webhook event reached the site and was accepted.
  const delivered = (await fakeEvents(request)).filter((e) => e.event.data.object.id === sessionId);
  expect(delivered.map((e) => e.event.type)).toEqual(["checkout.session.completed"]);
  expect(delivered[0].delivery.status).toBe(200);

  // Order page links.
  await expect(page.getByTestId("certificate-link")).toHaveAttribute("href", `/certificate/${licenseId}`);
  await expect(page.getByTestId("verify-link")).toHaveAttribute("href", new RegExp(`/en/verify/\\?id=${licenseId}$`));
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("license-download").click()]);
  expect(download.suggestedFilename()).toBe(`${licenseId}.json`);
  const signed = JSON.parse(fs.readFileSync(await download.path(), "utf8"));
  expect(signed.license.id).toBe(licenseId);
  expect(signed.license.product).toBe("pineforge-codegen");
  expect(signed.license.licensee.company).toBe(buyer.company);
  expect(signed.license.tier).toBe("team");
  expect(signed.license.option).toBe("seats-5");
  expect(signed.license.scope).toEqual({ seats: 5 });
  expect(signed.license.term.months).toBe(12);
  expect(signed.license.mode).toBe("test");
  expect(signed.signature.alg).toBe("Ed25519");
  expect(signed.signature.kid).toBe(process.env.E2E_KID ?? signed.signature.kid);
  expect(JSON.stringify(signed)).not.toContain(buyer.email);

  // Order API agrees.
  const order = await getOrder(request, sessionId);
  expect(order.status).toBe(200);
  expect(order.body.status).toBe("issued");
  expect(order.body.license.id).toBe(licenseId);
  expect(order.body.license.status).toBe("active");
  expect(order.body.order.amountSubtotal).toBe(240000);
  expect(order.body.order.currency).toBe("usd");
  expect(order.body.signedLicense).toEqual(signed);

  // Certificate: active, test mode.
  await page.goto(`/certificate/${licenseId}`);
  const cert = page.getByTestId("certificate");
  await expect(cert).toHaveAttribute("data-status", "active");
  await expect(cert).toHaveAttribute("data-mode", "test");
  await expect(page.getByTestId("license-id").first()).toContainText(licenseId);
  await expect(page.getByTestId("test-banner")).toBeVisible();
  await expect(page.getByTestId("revoked-banner")).toHaveCount(0);
  await expect(cert).toContainText(buyer.company);
  await expect(cert).not.toContainText(buyer.email);

  // Verify by id: valid (a test license on a test-mode deployment).
  await page.goto(`/en/verify/?id=${licenseId}`);
  const result = page.getByTestId("verify-result");
  await expect(result).toHaveAttribute("data-status", "valid");
  await expect(result).toHaveAttribute("data-valid", "true");
  await expect(page.getByTestId("verify-company")).toContainText(buyer.company);
  await expect(page.locator("main")).not.toContainText(buyer.email);
});

test("declined card 4000 0000 0000 0002: decline shown, no event, no license", async ({ page, request }) => {
  const buyer = makeBuyer("declined", test.info());
  const sessionId = await startCheckout(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });
  await payOnFakeCheckout(page, { card: "4000 0000 0000 0002", name: buyer.name });
  await expect(page.getByText("Your card was declined.")).toBeVisible();
  expect(new URL(page.url()).origin).toBe(new URL(E.stripe).origin);

  const rec = await fakeSession(request, sessionId);
  expect(rec.session.status).toBe("open");
  expect(rec.session.payment_status).toBe("unpaid");
  expect((await fakeEvents(request)).filter((e) => e.event.data.object.id === sessionId)).toHaveLength(0);

  const order = await getOrder(request, sessionId);
  expect(order.status).toBe(200);
  expect(order.body.status).toBe("pending");
  expect(order.body.license).toBeNull();
  expect(order.body.signedLicense).toBeNull();

  await page.goto(`/en/order/?session_id=${sessionId}`);
  await waitForOrderStatus(page, "pending", 15_000);
  await expect(page.getByTestId("license-id")).toHaveCount(0);

  const unknown = await getJson(request, `${E.base}/api/order?session_id=cs_test_doesnotexist`);
  expect(unknown.status).toBe(404);
  const missing = await getJson(request, `${E.base}/api/order`);
  expect(missing.status).toBe(400);
  expect(missing.body.error).toBe("missing_session_id");
});
