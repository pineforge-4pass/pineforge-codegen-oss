// Shared helpers for the E2E specs: run environment, the purchase flow through the UI, the fakes'
// control endpoints and small utilities. Specs import only from here and @playwright/test.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { expect } from "@playwright/test";

function required(name) {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set: run the suite through \`npm run e2e\` (e2e/run.mjs), which starts the site and the fakes.`);
  return v;
}

export const E = {
  get base() {
    return required("E2E_BASE_URL");
  },
  get live() {
    return required("E2E_LIVE_URL");
  },
  get stripe() {
    return required("E2E_STRIPE_URL");
  },
  get resend() {
    return required("E2E_RESEND_URL");
  },
  get webhookUrl() {
    return process.env.E2E_WEBHOOK_URL || `${required("E2E_BASE_URL")}/api/stripe/webhook`;
  },
  get webhookSecret() {
    return required("E2E_WEBHOOK_SECRET");
  },
  get keyringFile() {
    return required("E2E_KEYRING_FILE");
  },
  get kid() {
    return required("E2E_KID");
  },
  get runId() {
    return required("E2E_RUN_ID");
  },
  get siteDir() {
    return process.env.E2E_SITE_DIR || process.cwd();
  },
  get orphan() {
    return required("E2E_ORPHAN_URL");
  },
  get livePersistDir() {
    return required("E2E_LIVE_PERSIST_DIR");
  },
  get tmpDir() {
    return process.env.E2E_TMP_DIR || fs.mkdtempSync(path.join(process.env.TMPDIR || "/tmp", "pf-license-e2e-spec-"));
  },
};

export const LICENSE_URL = "https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/LICENSE";
export const NOTIFY_TO = "enterprise@pineforge.dev";
export const LICENSE_ID_RE = /^PFL-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$/;
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

export function commerceConfig() {
  return JSON.parse(fs.readFileSync(path.join(E.siteDir, "config", "commerce.json"), "utf8"));
}

/** Whitespace collapsed and Markdown emphasis (`*`, `**`, `***`) removed: how quotes are compared with LICENSE. */
export function normalizeText(s) {
  return String(s)
    .replace(/\*+/g, "")
    .replace(/ /g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function licenseText() {
  return normalizeText(fs.readFileSync(path.join(E.siteDir, "..", "LICENSE"), "utf8"));
}

/** A well-formed license id that no order produced (80 random bits, Crockford base32). */
export function randomLicenseId() {
  const bytes = crypto.randomBytes(16);
  let s = "";
  for (let i = 0; i < 16; i += 1) s += CROCKFORD[bytes[i] % 32];
  return `PFL-${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}`;
}

/** Per-test buyer data, namespaced with the run id so runs and tests never share rows. */
export function makeBuyer(label, testInfo) {
  const suffix = `${testInfo?.project?.name ?? "x"}-${crypto.randomBytes(3).toString("hex")}`;
  const tag = `${E.runId}-${label}-${suffix}`;
  return {
    tag,
    company: `${tag} Trading GmbH`,
    country: { code: "DE", name: "Germany" },
    name: "Ada Buyer",
    email: `${tag}@example.com`.toLowerCase(),
    reference: `PO-${suffix}`,
  };
}

export function cardExpiry() {
  return `12 / ${String((new Date().getUTCFullYear() + 3) % 100).padStart(2, "0")}`;
}

export function formatUsd(minor) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(minor / 100);
}

async function fillCountry(locator, country) {
  const tag = await locator.evaluate((el) => el.tagName.toLowerCase());
  if (tag !== "select") {
    await locator.fill(country.name);
    return;
  }
  const options = await locator.evaluate((el) => [...el.options].map((o) => ({ value: o.value, label: o.label.trim() })));
  const hit =
    options.find((o) => o.value.toUpperCase() === country.code) ??
    options.find((o) => o.label === country.name) ??
    options.find((o) => o.value);
  await locator.selectOption(hit.value);
}

/** Fills the checkout form through its labels (contract test hooks). */
export async function fillCheckoutForm(page, buyer, { accept = true } = {}) {
  await page.getByLabel("Company legal name", { exact: true }).fill(buyer.company);
  await fillCountry(page.getByLabel("Country", { exact: true }), buyer.country);
  await page.getByLabel("Your name", { exact: true }).fill(buyer.name);
  await page.getByLabel("Email", { exact: true }).fill(buyer.email);
  await page.getByLabel("Purchase order or reference (optional)", { exact: true }).fill(buyer.reference);
  const box = page.getByLabel("I accept the Commercial License Agreement (draft)", { exact: true });
  if (accept) await box.check();
  else await box.uncheck();
}

/** plans -> Buy -> checkout form -> submit; ends on the fake hosted checkout page. Returns the session id. */
export async function startCheckout(page, { tier = "team", option = "seats-5", buyer, fromPlans = true }) {
  if (fromPlans) {
    await page.goto("/en/plans/");
    const buy = page.getByTestId(`buy-${tier}-${option}`);
    await expect(buy).toHaveAttribute("href", new RegExp(`/en/checkout/\\?tier=${tier}&option=${option}$`));
    await buy.click();
    await page.waitForURL((u) => u.pathname === "/en/checkout/" && u.searchParams.get("tier") === tier && u.searchParams.get("option") === option);
  } else {
    await page.goto(`/en/checkout/?tier=${tier}&option=${option}`);
  }
  await expect(page.getByTestId("order-summary")).toBeVisible();
  await fillCheckoutForm(page, buyer);
  await page.getByTestId("checkout-submit").click();
  const stripeOrigin = new URL(E.stripe).origin;
  await page.waitForURL((u) => u.origin === stripeOrigin && /^\/pay\/cs_test_/.test(u.pathname), { timeout: 45_000 });
  return new URL(page.url()).pathname.split("/").pop();
}

/** Fills the fake hosted checkout and presses Pay. */
export async function payOnFakeCheckout(page, { card = "4242 4242 4242 4242", name = "Ada Buyer", email } = {}) {
  const emailBox = page.getByLabel("Email", { exact: true });
  if (email || !(await emailBox.inputValue())) await emailBox.fill(email ?? "buyer@example.com");
  await page.getByLabel("Card number", { exact: true }).fill(card);
  await page.getByLabel("Expiration (MM / YY)", { exact: true }).fill(cardExpiry());
  await page.getByLabel("CVC", { exact: true }).fill("123");
  await page.getByLabel("Cardholder name", { exact: true }).fill(name);
  await page.getByLabel("Country or region", { exact: true }).selectOption("DE");
  await page.getByRole("button", { name: "Pay", exact: true }).click();
}

export async function waitForOrderStatus(page, status, timeout = 60_000) {
  await expect(page.getByTestId("order-status")).toHaveAttribute("data-status", status, { timeout });
}

/** The whole purchase through the UI with card 4242; returns ids once the order page shows the license. */
export async function purchase(page, { tier = "team", option = "seats-5", buyer, fromPlans = true }) {
  const sessionId = await startCheckout(page, { tier, option, buyer, fromPlans });
  await payOnFakeCheckout(page, { name: buyer.name });
  await page.waitForURL((u) => u.origin === new URL(E.base).origin && u.pathname === "/en/order/" && u.searchParams.get("session_id") === sessionId, {
    timeout: 45_000,
  });
  await waitForOrderStatus(page, "issued");
  const licenseId = (await page.getByTestId("license-id").first().innerText()).trim();
  expect(licenseId).toMatch(LICENSE_ID_RE);
  return { sessionId, licenseId, orderUrl: page.url() };
}

export async function getJson(request, url, init) {
  const r = await request.get(url, init);
  const text = await r.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { _raw: text };
  }
  return { status: r.status(), body, text, headers: r.headers() };
}

export async function postJson(request, url, data, init = {}) {
  const r = await request.post(url, { data, ...init });
  const text = await r.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { _raw: text };
  }
  return { status: r.status(), body, text, headers: r.headers() };
}

export function checkoutBody(buyer, { tier = "team", option = "seats-5", pf_hp = "" } = {}) {
  return {
    tier,
    option,
    company: buyer.company,
    country: buyer.country.code,
    buyerName: buyer.name,
    buyerEmail: buyer.email,
    reference: buyer.reference,
    acceptAgreement: true,
    locale: "en",
    pf_hp,
  };
}

/** POST /api/checkout over HTTP (no browser); returns the Stripe session id from the returned url. */
export async function apiCheckout(request, buyer, opts = {}, baseUrl = E.base) {
  const res = await postJson(request, `${baseUrl}/api/checkout`, checkoutBody(buyer, opts));
  expect(res.status, res.text).toBe(200);
  const url = new URL(res.body.url);
  expect(url.origin).toBe(new URL(E.stripe).origin);
  return { sessionId: url.pathname.split("/").pop(), url: res.body.url };
}

export async function getOrder(request, sessionId) {
  return getJson(request, `${E.base}/api/order?session_id=${encodeURIComponent(sessionId)}`);
}

export async function fakeSession(request, sessionId) {
  const res = await getJson(request, `${E.stripe}/__control/sessions/${sessionId}`);
  expect(res.status, res.text).toBe(200);
  return res.body;
}

export async function fakeEvents(request) {
  return (await getJson(request, `${E.stripe}/__control/events`)).body.events;
}

export async function sendEvent(request, payload) {
  const res = await postJson(request, `${E.stripe}/__control/send-event`, payload);
  expect(res.status, res.text).toBe(200);
  return res.body;
}

/** Re-posts the SAME checkout.session.completed event of a session (fresh signature), as a Stripe retry. */
export async function redeliver(request, sessionId) {
  const res = await postJson(request, `${E.stripe}/__control/redeliver`, { session_id: sessionId });
  expect(res.status, res.text).toBe(200);
  return res.body;
}

/** The next `count` emails addressed to `to` answer `status` (default 503) in the sink and are not stored. */
export async function failNextEmails(request, { count = 1, status = 503, to }) {
  const res = await postJson(request, `${E.resend}/__control/fail-next`, { count, status, to });
  expect(res.status, res.text).toBe(200);
}

export async function refund(request, paymentIntent, amount) {
  const res = await postJson(request, `${E.stripe}/__control/refund`, amount === undefined ? { payment_intent: paymentIntent } : { payment_intent: paymentIntent, amount });
  expect(res.status, res.text).toBe(200);
  return res.body;
}

/** A signed charge.dispute.created / charge.dispute.closed for the charge of `paymentIntent` (one dispute per charge). */
export async function dispute(request, paymentIntent, action, status) {
  const res = await postJson(request, `${E.stripe}/__control/dispute`, { payment_intent: paymentIntent, action, ...(status ? { status } : {}) });
  expect(res.status, res.text).toBe(200);
  return res.body;
}

/** Pays a session on the fake hosted page over HTTP (no browser); the fake answers 303 to the success url. */
export async function payOverHttp(request, sessionId, { card = "4242 4242 4242 4242", email = "buyer@example.com" } = {}) {
  const r = await request.post(`${E.stripe}/pay/${sessionId}`, {
    form: { email, cardNumber: card, expiry: cardExpiry().replace(/ /g, ""), cvc: "123", name: "Ada Buyer", country: "DE" },
    maxRedirects: 0,
  });
  expect(r.status(), await r.text()).toBe(303);
}

/** Polls GET /api/order until the order reaches `status`; returns its body. */
export async function waitForOrderApi(request, sessionId, status = "issued", timeout = 45_000) {
  let body;
  await expect
    .poll(async () => {
      body = (await getOrder(request, sessionId)).body;
      return body.status;
    }, { timeout })
    .toBe(status);
  return body;
}

export const isAlert = (m) => /^(\[TEST\] )?ALERT:/.test(String(m.subject));

export async function listEmails(request) {
  return (await getJson(request, `${E.resend}/__control/emails`)).body.emails ?? [];
}

export const recipients = (email) => (Array.isArray(email.to) ? email.to : [email.to]).map((t) => String(t).toLowerCase());

/** Polls the Resend sink until `predicate` matches `min` emails; returns the matches. */
export async function waitForEmails(request, predicate, { min = 1, timeout = 30_000 } = {}) {
  const deadline = Date.now() + timeout;
  let hits = [];
  while (Date.now() < deadline) {
    hits = (await listEmails(request)).filter(predicate);
    if (hits.length >= min) return hits;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`expected at least ${min} matching email(s) in the sink within ${timeout} ms, found ${hits.length}`);
}

export function decodeAttachment(att) {
  return Buffer.from(att.content, "base64").toString("utf8");
}

/** A paid copy of a fake session, as Stripe would send it in checkout.session.completed. */
export function paidSessionObject(session, overrides = {}) {
  return {
    ...session,
    status: "complete",
    payment_status: "paid",
    payment_intent: `pi_forged${crypto.randomBytes(8).toString("hex")}`,
    customer: `cus_forged${crypto.randomBytes(4).toString("hex")}`,
    ...overrides,
  };
}

/** Changes one character in the middle of a base64url string (the last char may only carry padding bits). */
export function alterSignature(value) {
  const i = Math.floor(value.length / 2);
  const c = value[i] === "A" ? "B" : "A";
  return value.slice(0, i) + c + value.slice(i + 1);
}

export function screensDir() {
  const dir = path.join(E.siteDir, "e2e", "artifacts", "screens");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export async function shot(page, name, variant) {
  await page.evaluate(async () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    await document.fonts?.ready;
  });
  await page.screenshot({ path: path.join(screensDir(), `${name}-${variant}.png`), fullPage: true });
}
