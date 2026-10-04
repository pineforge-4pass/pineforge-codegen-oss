// 6. Live-payment guard: a build holding a live Stripe key fails before it builds anything while the
// agreement is a draft (or prices are placeholders / no seller is named), and a deployment running with
// a live key refuses checkout with 503 without calling Stripe.
import { test, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { E, checkoutBody, fillCheckoutForm, getJson, makeBuyer, postJson } from "../support/e2e.mjs";

function buildWithLiveKey() {
  return new Promise((resolve) => {
    const env = { ...process.env, STRIPE_SECRET_KEY: "sk_live_x" };
    delete env.STRIPE_PUBLISHABLE_KEY;
    const child = spawn(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "build"], { cwd: E.siteDir, env, detached: true, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let pastGuard = false;
    const onData = (d) => {
      out += d;
      // npm echoes each script before it runs it. Reaching `next build` means the guard let it through:
      // stop at once so the running suite's out/ is left alone.
      if (!pastGuard && /^> next build/m.test(out)) {
        pastGuard = true;
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {}
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    const timer = setTimeout(() => {
      try {
        process.kill(-child.pid, "SIGKILL");
      } catch {}
    }, 120_000);
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      resolve({ code: code ?? (signal ? 1 : 0), out, pastGuard });
    });
  });
}

test("npm run build with STRIPE_SECRET_KEY=sk_live_x fails in the guard and says why", async () => {
  const { code, out, pastGuard } = await buildWithLiveKey();
  expect(pastGuard, `the build went past the guard:\n${out}`).toBe(false);
  expect(code, out).not.toBe(0);
  expect(out).toMatch(/live/i);
  expect(out).toContain("STRIPE_SECRET_KEY");
  expect(out).toMatch(/DRAFT — requires review by counsel before go-live|pricesArePlaceholders|legalName/);
});

test("a deployment with a live key answers checkout with 503 and never calls Stripe", async ({ request, browser }) => {
  const buyer = makeBuyer("live-guard", test.info());
  const res = await postJson(request, `${E.live}/api/checkout`, checkoutBody(buyer));
  expect(res.status, res.text).toBe(503);
  expect(res.body.error).toBe("live_payments_disabled");
  expect(res.headers["cache-control"] ?? "").toContain("no-store");

  // The same through the checkout page of that deployment: an error, no redirect.
  const context = await browser.newContext({ baseURL: E.live, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto("/en/checkout/?tier=team&option=seats-5");
  await expect(page.getByTestId("order-summary")).toBeVisible();
  await fillCheckoutForm(page, buyer);
  await page.getByTestId("checkout-submit").click();
  await expect(page.getByTestId("form-error")).toBeVisible();
  await expect(page.getByTestId("form-error")).toHaveAttribute("role", "alert");
  expect(new URL(page.url()).origin).toBe(new URL(E.live).origin);
  await context.close();

  const { requests } = (await getJson(request, `${E.stripe}/__control/requests`)).body;
  expect(requests.filter((r) => r.auth.startsWith("sk_live_"))).toEqual([]);
});
