// Site basics over HTTP and in the browser: the root redirect, security headers, the published keyring,
// API response headers, and the order page's missing / not-found states.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { E, checkoutBody, getJson, makeBuyer } from "../support/e2e.mjs";

test("/ redirects to /en/", async ({ page, request }) => {
  const r = await request.get(`${E.base}/`, { maxRedirects: 0 });
  expect([301, 302, 307, 308]).toContain(r.status());
  expect(new URL(r.headers().location, E.base).pathname).toBe("/en/");
  await page.goto("/");
  await expect(page).toHaveURL(/\/en\/$/);
});

test("pages carry the security headers", async ({ request }) => {
  for (const url of ["/en/", "/en/checkout/"]) {
    const h = (await request.get(`${E.base}${url}`)).headers();
    expect(h["x-frame-options"], url).toBe("DENY");
    expect(h["x-content-type-options"], url).toBe("nosniff");
    expect(h["referrer-policy"], url).toBeTruthy();
    expect(h["content-security-policy"] ?? "", url).toContain("frame-ancestors 'none'");
  }
});

test("the keyring is published at /.well-known and equals the bundled one", async ({ request }) => {
  const res = await getJson(request, `${E.base}/.well-known/pineforge-license-keys.json`);
  expect(res.status).toBe(200);
  const bundled = JSON.parse(fs.readFileSync(path.join(E.siteDir, "keys", "license-public-keys.json"), "utf8"));
  expect(res.body).toEqual(bundled);
  for (const k of res.body.keys) {
    expect(k).toMatchObject({ kty: "OKP", crv: "Ed25519" });
    expect(k.d, "no private key material").toBeUndefined();
  }
});

test("API answers are JSON and not cached", async ({ request }) => {
  for (const url of ["/api/verify", "/api/order", "/api/verify?id=not-a-license"]) {
    const r = await request.get(`${E.base}${url}`);
    expect(r.headers()["content-type"] ?? "", url).toContain("application/json");
    expect(r.headers()["cache-control"] ?? "", url).toContain("no-store");
  }
  const bad = await request.post(`${E.base}/api/checkout`, { headers: { "Content-Type": "application/json" }, data: Buffer.from("{not json") });
  expect(bad.status()).toBe(400);
  expect((await bad.json()).error).toBe("invalid_json");
  const plan = await request.post(`${E.base}/api/checkout`, { data: checkoutBody(makeBuyer("invalid-plan", test.info()), { option: "seats-999" }) });
  expect(plan.status()).toBe(400);
  expect((await plan.json()).error).toBe("invalid_plan");
});

test("order page without a session id, and with an unknown one", async ({ page }) => {
  await page.goto("/en/order/");
  await expect(page.getByTestId("order-status")).toHaveAttribute("data-status", "missing");
  await page.goto("/en/order/?session_id=cs_test_unknown");
  await expect(page.getByTestId("order-status")).toHaveAttribute("data-status", "not_found");
  await expect(page.getByTestId("license-id")).toHaveCount(0);
});
