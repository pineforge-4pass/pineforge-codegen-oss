// Plans ledger and the checkout page (desktop and phone): tiers and options from config/commerce.json,
// Buy links, the order summary, an invalid plan, inline field errors and the honeypot.
import { test, expect } from "@playwright/test";
import { commerceConfig, fillCheckoutForm, formatUsd, makeBuyer } from "../support/e2e.mjs";

const config = commerceConfig();
const digits = (minor) => formatUsd(minor).replace(/\.00$/, ""); // "$2,400" matches "$2,400" and "$2,400.00"

test.describe("plans", () => {
  test("every tier and option from the config, with prices and Buy links", async ({ page }) => {
    await page.goto("/en/plans/");
    await expect(page.getByTestId("test-mode-notice")).toBeVisible();
    for (const tier of config.tiers) {
      const section = page.locator(`#tier-${tier.id}`);
      await expect(section).toBeVisible();
      await expect(section).toHaveAttribute("data-testid", `tier-${tier.id}`);
      for (const option of tier.options) {
        const row = page.getByTestId(`option-${tier.id}-${option.id}`);
        await expect(row).toBeVisible();
        await expect(row).toContainText(digits(option.annual));
        const buy = page.getByTestId(`buy-${tier.id}-${option.id}`);
        await expect(buy).toBeVisible();
        await expect(buy).toHaveAttribute("href", `/en/checkout/?tier=${tier.id}&option=${option.id}`);
      }
    }
    const quote = page.getByTestId("quote-link").first();
    await expect(quote).toBeVisible();
    await expect(quote).toHaveAttribute("href", /\/en\/contact\//);
  });

  test("Buy leads to the checkout for that option", async ({ page }) => {
    await page.goto("/en/plans/");
    await page.getByTestId("buy-fund-aum-25m").click();
    await page.waitForURL((u) => u.pathname === "/en/checkout/" && u.searchParams.get("tier") === "fund" && u.searchParams.get("option") === "aum-25m");
    const fund = config.tiers.find((t) => t.id === "fund").options.find((o) => o.id === "aum-25m");
    await expect(page.getByTestId("order-summary")).toBeVisible();
    await expect(page.getByTestId("order-price")).toContainText(digits(fund.annual));
  });
});

test.describe("checkout", () => {
  test("order summary is priced from the config", async ({ page }) => {
    await page.goto("/en/checkout/?tier=team&option=seats-5");
    const summary = page.getByTestId("order-summary");
    await expect(summary).toBeVisible();
    await expect(summary.getByTestId("order-product")).toContainText("Team");
    await expect(summary.getByTestId("order-product")).toContainText("5 seats");
    await expect(summary.getByTestId("order-price")).toContainText(digits(240000));
    await expect(page.getByTestId("checkout-submit")).toHaveText(/Continue to payment/);
    await expect(page.getByTestId("checkout-invalid")).toHaveCount(0);
  });

  test("an unknown plan shows checkout-invalid and no form", async ({ page }) => {
    await page.goto("/en/checkout/?tier=team&option=seats-999");
    await expect(page.getByTestId("checkout-invalid")).toBeVisible();
    await expect(page.getByTestId("checkout-submit")).toHaveCount(0);
    await page.goto("/en/checkout/");
    await expect(page.getByTestId("checkout-invalid")).toBeVisible();
  });

  test("empty and invalid fields show inline errors and stay on the page", async ({ page }) => {
    await page.goto("/en/checkout/?tier=team&option=seats-5");
    await expect(page.getByTestId("order-summary")).toBeVisible();
    await page.getByTestId("checkout-submit").click();
    for (const field of ["company", "country", "buyerName", "buyerEmail", "acceptAgreement"]) {
      const error = page.getByTestId(`error-${field}`);
      await expect(error, `error-${field}`).toBeVisible();
      const input = page.locator(`[name="${field}"]`).first();
      await expect(input).toHaveAttribute("aria-invalid", "true");
      const errorId = await error.getAttribute("id");
      expect(errorId, `error-${field} needs an id for aria-describedby`).toBeTruthy();
      await expect(input).toHaveAttribute("aria-describedby", new RegExp(`(^|\\s)${errorId}(\\s|$)`));
    }
    await expect(page).toHaveURL(/\/en\/checkout\//);

    const buyer = makeBuyer("checkout-invalid", test.info());
    await fillCheckoutForm(page, { ...buyer, email: "not-an-email" });
    await page.getByTestId("checkout-submit").click();
    await expect(page.getByTestId("error-buyerEmail")).toBeVisible();
    await expect(page.getByTestId("error-company")).toHaveCount(0);
    await expect(page).toHaveURL(/\/en\/checkout\//);
  });

  test("the honeypot is out of the tab order and autocomplete", async ({ page }) => {
    await page.goto("/en/checkout/?tier=team&option=seats-5");
    const trap = page.locator('input[name="website"]');
    await expect(trap).toHaveCount(1);
    await expect(trap).toHaveAttribute("tabindex", "-1");
    await expect(trap).toHaveAttribute("autocomplete", "off");
  });
});
