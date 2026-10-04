// 8. Accessibility: axe-core finds no serious or critical violation on any page (light and dark).
import { test, expect } from "@playwright/test";
import path from "node:path";
import { E, makeBuyer, purchase } from "../support/e2e.mjs";

const AXE = () => path.join(E.siteDir, "node_modules", "axe-core", "axe.min.js");

// The certificate page (a Function) sends a nonce-based CSP that would block the injected axe script.
test.use({ bypassCSP: true });

async function audit(page, label) {
  await page.addScriptTag({ path: AXE() });
  const violations = await page.evaluate(async () => {
    const r = await window.axe.run(document, { resultTypes: ["violations"] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
  });
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking, `${label}: ${JSON.stringify(blocking, null, 2)}`).toEqual([]);
}

const STATIC_PAGES = [
  ["home", "/en/"],
  ["plans", "/en/plans/"],
  ["checkout", "/en/checkout/?tier=team&option=seats-5"],
  ["verify", "/en/verify/"],
  ["faq", "/en/faq/"],
  ["terms", "/en/terms/"],
  ["contact", "/en/contact/"],
];

for (const scheme of ["light", "dark"]) {
  for (const [name, url] of STATIC_PAGES) {
    test(`${name} (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(url);
      await expect(page.locator("main#main")).toBeVisible();
      if (name === "home") await expect(page.getByTestId("guide-interactive")).toBeVisible();
      if (name === "checkout") await expect(page.getByTestId("order-summary")).toBeVisible();
      await audit(page, `${name} (${scheme})`);
    });
  }
}

test("checkout with field errors, terms banner", async ({ page }) => {
  await page.goto("/en/checkout/?tier=team&option=seats-5");
  await page.getByTestId("checkout-submit").click();
  await expect(page.getByTestId("error-company")).toBeVisible();
  await audit(page, "checkout with errors");
  await page.goto("/en/terms/");
  await expect(page.getByTestId("agreement")).toBeVisible();
  await expect(page.getByTestId("agreement-draft-banner")).toBeVisible();
});

test("order (issued), certificate and verify result after a purchase", async ({ page }) => {
  const buyer = makeBuyer("axe", test.info());
  const { licenseId } = await purchase(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });
  await audit(page, "order (issued)");
  await page.goto(`/certificate/${licenseId}`);
  await expect(page.getByTestId("certificate")).toBeVisible();
  await audit(page, "certificate");
  await page.emulateMedia({ colorScheme: "dark" });
  await audit(page, "certificate (dark)");
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(`/en/verify/?id=${licenseId}`);
  await expect(page.getByTestId("verify-result")).toHaveAttribute("data-status", "valid");
  await audit(page, "verify (valid)");
});
