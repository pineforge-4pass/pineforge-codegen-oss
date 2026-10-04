// 4. Verify: a genuine license is valid; tampered copies fail the signature; an unknown key, an unknown
// id and malformed input get their own status. Webhook calls without a valid signature (or with the
// wrong livemode, or a wrong amount) never issue a license.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  E,
  NOTIFY_TO,
  alterSignature,
  apiCheckout,
  fakeSession,
  getJson,
  getOrder,
  makeBuyer,
  paidSessionObject,
  postJson,
  purchase,
  randomLicenseId,
  recipients,
  sendEvent,
  waitForEmails,
} from "../support/e2e.mjs";

const clone = (x) => JSON.parse(JSON.stringify(x));

test("genuine, tampered, unknown-key, unknown-id and malformed licenses", async ({ page, request }) => {
  const buyer = makeBuyer("verify", test.info());
  const { sessionId, licenseId } = await purchase(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });
  const signed = (await getOrder(request, sessionId)).body.signedLicense;
  expect(signed.license.id).toBe(licenseId);

  // Through the page: paste the genuine JSON -> valid.
  await page.goto("/en/verify/");
  await page.getByLabel("Signed license JSON", { exact: true }).fill(JSON.stringify(signed, null, 2));
  await page.getByTestId("verify-json-submit").click();
  const result = page.getByTestId("verify-result");
  await expect(result).toHaveAttribute("data-status", "valid");
  await expect(result).toHaveAttribute("data-valid", "true");
  await expect(page.getByTestId("verify-company")).toContainText(buyer.company);

  // Through the page: company changed -> invalid_signature.
  const companyChanged = clone(signed);
  companyChanged.license.licensee.company = `${buyer.company} Holdings`;
  await page.getByLabel("Signed license JSON", { exact: true }).fill(JSON.stringify(companyChanged));
  await page.getByTestId("verify-json-submit").click();
  await expect(result).toHaveAttribute("data-status", "invalid_signature");
  await expect(result).toHaveAttribute("data-valid", "false");

  // Through the page: upload the genuine file -> valid.
  await page.goto("/en/verify/");
  const dir = fs.mkdtempSync(path.join(E.tmpDir, "verify-"));
  const file = path.join(dir, `${licenseId}.json`);
  fs.writeFileSync(file, JSON.stringify(signed, null, 2));
  await page.getByLabel("Or upload the license file", { exact: true }).setInputFiles(file);
  const statusAfterUpload = await result
    .getAttribute("data-status", { timeout: 3000 })
    .catch(() => null);
  if (statusAfterUpload !== "valid") await page.getByTestId("verify-json-submit").click();
  await expect(result).toHaveAttribute("data-status", "valid");

  // Through the page: an unknown, well-formed id -> not_found.
  await page.goto("/en/verify/");
  await page.getByLabel("License id", { exact: true }).fill(randomLicenseId());
  await page.getByTestId("verify-id-submit").click();
  await expect(result).toHaveAttribute("data-status", "not_found");
  await expect(result).toHaveAttribute("data-valid", "false");

  // Over HTTP: every tampering and status.
  const post = (body) => postJson(request, `${E.base}/api/verify`, body);
  const genuine = await post(signed);
  expect(genuine.status).toBe(200);
  expect(genuine.body).toMatchObject({ valid: true, status: "valid", mode: "test" });
  expect(genuine.body.license.id).toBe(licenseId);
  expect(genuine.text).not.toContain(buyer.email);

  const extended = clone(signed);
  extended.license.term.validUntil = "2099-12-31T00:00:00.000Z";
  expect((await post(extended)).body).toMatchObject({ valid: false, status: "invalid_signature" });

  expect((await post(companyChanged)).body).toMatchObject({ valid: false, status: "invalid_signature" });

  const sigAltered = clone(signed);
  sigAltered.signature.value = alterSignature(signed.signature.value);
  expect((await post(sigAltered)).body).toMatchObject({ valid: false, status: "invalid_signature" });

  const unknownKid = clone(signed);
  unknownKid.signature.kid = "pfl-e2e-unknown-key";
  expect((await post(unknownKid)).body).toMatchObject({ valid: false, status: "unknown_key" });

  const malformed = await post({ license: { id: licenseId } });
  expect(malformed.status).toBe(200);
  expect(malformed.body).toMatchObject({ valid: false, status: "malformed" });

  const byId = await getJson(request, `${E.base}/api/verify?id=${licenseId}`);
  expect(byId.body).toMatchObject({ valid: true, status: "valid", mode: "test" });
  expect(byId.body.license.licensee.company).toBe(buyer.company);
  expect(byId.text).not.toContain(buyer.email);
  const byLowerId = await getJson(request, `${E.base}/api/verify?id=${licenseId.toLowerCase()}`);
  expect(byLowerId.body.status).toBe("valid");

  const notFound = await getJson(request, `${E.base}/api/verify?id=${randomLicenseId()}`);
  expect(notFound.status).toBe(200);
  expect(notFound.body).toMatchObject({ valid: false, status: "not_found", license: null });
  const badId = await getJson(request, `${E.base}/api/verify?id=not-a-license`);
  expect(badId.body).toMatchObject({ valid: false, status: "malformed" });
  const noId = await getJson(request, `${E.base}/api/verify`);
  expect(noId.status).toBe(400);
  expect(noId.body.error).toBe("missing_id");

  // Certificate for an unknown id is a 404 page.
  const cert404 = await request.get(`${E.base}/certificate/${randomLicenseId()}`);
  expect(cert404.status()).toBe(404);
});

test("webhook: bad, missing or stale signatures and a livemode event are refused; nothing is issued", async ({ request }) => {
  const buyer = makeBuyer("webhook-sig", test.info());
  const { sessionId } = await apiCheckout(request, buyer);
  const forged = paidSessionObject((await fakeSession(request, sessionId)).session);

  for (const signature of ["bad", "none", "stale", "wrong-secret"]) {
    const { delivery } = await sendEvent(request, { type: "checkout.session.completed", object: forged, signature });
    expect(delivery.status, `signature=${signature}: ${delivery.body}`).toBe(400);
    expect(JSON.parse(delivery.body).error).toBe("invalid_signature");
  }

  // Straight to the webhook, without any Stripe-Signature header.
  const direct = await request.post(`${E.base}/api/stripe/webhook`, {
    headers: { "Content-Type": "application/json" },
    data: Buffer.from(JSON.stringify({ id: "evt_direct", object: "event", type: "checkout.session.completed", livemode: false, data: { object: forged } })),
  });
  expect(direct.status()).toBe(400);
  expect((await direct.json()).error).toBe("invalid_signature");

  // Correctly signed but livemode does not match this test-mode deployment.
  const live = await sendEvent(request, { type: "checkout.session.completed", object: { ...forged, livemode: true }, livemode: true });
  expect(live.delivery.status, live.delivery.body).toBe(400);
  expect(JSON.parse(live.delivery.body).error).toBe("livemode_mismatch");

  const order = await getOrder(request, sessionId);
  expect(order.status).toBe(200);
  expect(order.body.status).toBe("pending");
  expect(order.body.license).toBeNull();
  expect(order.body.signedLicense).toBeNull();
});

test("webhook: a paid session whose amount differs from the order is a mismatch, alerted, not issued", async ({ request }) => {
  const buyer = makeBuyer("webhook-amount", test.info());
  const { sessionId } = await apiCheckout(request, buyer);
  const session = (await fakeSession(request, sessionId)).session;
  const forged = paidSessionObject(session, { amount_subtotal: 100, amount_total: 100 });
  const { delivery } = await sendEvent(request, { type: "checkout.session.completed", object: forged });
  expect(delivery.status, delivery.body).toBe(200);

  await expect
    .poll(async () => (await getOrder(request, sessionId)).body.status, { timeout: 15_000 })
    .toBe("mismatch");
  const order = await getOrder(request, sessionId);
  expect(order.body.license).toBeNull();
  const alerts = await waitForEmails(
    request,
    (m) =>
      /ALERT:/.test(String(m.subject)) &&
      recipients(m).includes(NOTIFY_TO) &&
      (JSON.stringify(m).includes(sessionId) || JSON.stringify(m).includes(session.client_reference_id)),
  );
  expect(alerts.length).toBeGreaterThan(0);
});
