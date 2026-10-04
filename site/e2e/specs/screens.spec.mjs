// Full-page screenshots (desktop 1440 px and phone 390 px) of home, plans, checkout, the fake hosted
// checkout, order confirmation, certificate and verify, into e2e/artifacts/screens/ (gitignored).
import { test, expect } from "@playwright/test";
import {
  E,
  cardExpiry,
  fillCheckoutForm,
  makeBuyer,
  payOnFakeCheckout,
  shot,
  startCheckout,
  waitForOrderStatus,
} from "../support/e2e.mjs";

test("screenshots, desktop and phone", async ({ page, browser }) => {
  test.setTimeout(240_000);
  const buyer = makeBuyer("screens", test.info());

  // Desktop (this project's 1440 x 900 page), along a real purchase.
  await page.goto("/en/");
  await expect(page.getByTestId("guide-interactive")).toBeVisible();
  await shot(page, "home", "desktop-1440");
  await page.goto("/en/plans/");
  await shot(page, "plans", "desktop-1440");
  await page.goto("/en/checkout/?tier=team&option=seats-5");
  await expect(page.getByTestId("order-summary")).toBeVisible();
  await fillCheckoutForm(page, buyer);
  await shot(page, "checkout", "desktop-1440");
  const sessionId = await startCheckout(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });
  await page.getByLabel("Card number", { exact: true }).fill("4242 4242 4242 4242");
  await page.getByLabel("Expiration (MM / YY)", { exact: true }).fill(cardExpiry());
  await page.getByLabel("CVC", { exact: true }).fill("123");
  await page.getByLabel("Cardholder name", { exact: true }).fill(buyer.name);
  await shot(page, "hosted-checkout", "desktop-1440");
  await payOnFakeCheckout(page, { name: buyer.name });
  await page.waitForURL((u) => u.pathname === "/en/order/" && u.searchParams.get("session_id") === sessionId);
  await waitForOrderStatus(page, "issued");
  const licenseId = (await page.getByTestId("license-id").first().innerText()).trim();
  await shot(page, "order", "desktop-1440");
  await page.goto(`/certificate/${licenseId}`);
  await expect(page.getByTestId("certificate")).toBeVisible();
  await shot(page, "certificate", "desktop-1440");
  await page.goto(`/en/verify/?id=${licenseId}`);
  await expect(page.getByTestId("verify-result")).toHaveAttribute("data-status", "valid");
  await shot(page, "verify", "desktop-1440");

  // Phone (390 x 844, touch). The order, certificate and verify pages reuse the purchase above.
  const phone = await browser.newContext({
    baseURL: E.base,
    viewport: { width: 390, height: 844 },
    screen: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const m = await phone.newPage();
  await m.goto("/en/");
  await expect(m.getByTestId("guide-interactive")).toBeVisible();
  await shot(m, "home", "mobile-390");
  await m.goto("/en/plans/");
  await shot(m, "plans", "mobile-390");
  await m.goto("/en/checkout/?tier=team&option=seats-5");
  await expect(m.getByTestId("order-summary")).toBeVisible();
  const phoneBuyer = makeBuyer("screens-phone", test.info());
  await fillCheckoutForm(m, phoneBuyer);
  await shot(m, "checkout", "mobile-390");
  await startCheckout(m, { tier: "team", option: "seats-5", buyer: phoneBuyer, fromPlans: false });
  await shot(m, "hosted-checkout", "mobile-390");
  await m.goto(`/en/order/?session_id=${sessionId}`);
  await waitForOrderStatus(m, "issued");
  await shot(m, "order", "mobile-390");
  await m.goto(`/certificate/${licenseId}`);
  await expect(m.getByTestId("certificate")).toBeVisible();
  await shot(m, "certificate", "mobile-390");
  await m.goto(`/en/verify/?id=${licenseId}`);
  await expect(m.getByTestId("verify-result")).toHaveAttribute("data-status", "valid");
  await shot(m, "verify", "mobile-390");
  await phone.close();
});
