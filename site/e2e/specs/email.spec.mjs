// 3. Email sink: a purchase sends the buyer's license email (id, certificate link, verify link, the
// signed license attached) and the sale notice to enterprise@pineforge.dev. The attachment verifies
// offline with scripts/verify-license.mjs against this run's key.
import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { E, NOTIFY_TO, decodeAttachment, makeBuyer, purchase, recipients, waitForEmails } from "../support/e2e.mjs";

function verifyLicenseCli(file, keyringFile) {
  const args = ["scripts/verify-license.mjs", file];
  if (keyringFile) args.push("--keyring", keyringFile);
  const r = spawnSync(process.execPath, args, { cwd: E.siteDir, encoding: "utf8", timeout: 60_000, env: { ...process.env, LICENSE_PUBLIC_KEYS: "" } });
  return { status: r.status, out: `${r.stdout}\n${r.stderr}` };
}

test("buyer email with the signed license attached, and the sale notice", async ({ page, request }) => {
  const buyer = makeBuyer("email", test.info());
  const { licenseId } = await purchase(page, { tier: "team", option: "seats-5", buyer });

  const [toBuyer] = await waitForEmails(request, (m) => recipients(m).includes(buyer.email));
  expect(toBuyer.subject).toBe(`[TEST] Your PineForge Codegen commercial license ${licenseId}`);
  expect(toBuyer.from).toContain("enterprise@pineforge.dev");
  expect(toBuyer.text).toContain(licenseId);
  expect(toBuyer.text).toContain(`${E.base}/certificate/${licenseId}`);
  expect(toBuyer.text).toMatch(new RegExp(`/verify/\\?id=${licenseId}`));
  const attachment = (toBuyer.attachments ?? []).find((a) => a.filename === `${licenseId}.json`);
  expect(attachment, `attachment ${licenseId}.json`).toBeTruthy();
  const json = decodeAttachment(attachment);
  const signed = JSON.parse(json);
  expect(signed.license.id).toBe(licenseId);
  expect(signed.license.licensee.company).toBe(buyer.company);
  expect(signed.license.mode).toBe("test");
  expect(json).not.toContain(buyer.email);

  const [notice] = await waitForEmails(request, (m) => recipients(m).includes(NOTIFY_TO) && String(m.subject).includes(licenseId));
  expect(notice.subject).toBe(`[TEST] License sale: ${licenseId} — ${buyer.company} (team/seats-5)`);

  // Exactly the two emails for this license.
  const all = await waitForEmails(request, (m) => String(m.subject).includes(licenseId), { min: 2 });
  expect(all).toHaveLength(2);

  // Offline verification of the attached file.
  const dir = fs.mkdtempSync(path.join(E.tmpDir, "email-"));
  const file = path.join(dir, `${licenseId}.json`);
  fs.writeFileSync(file, json);
  const ok = verifyLicenseCli(file, E.keyringFile);
  expect(ok.status, ok.out).toBe(0);
  expect(ok.out).toMatch(/Signature: valid/);
  expect(ok.out).not.toMatch(/INVALID/);
  expect(ok.out).toMatch(/TEST LICENSE/);
  expect(ok.out).toMatch(/not a commercial license/);

  // A tampered copy fails, and the shipped (production) keyring does not trust the run's test key.
  const tampered = path.join(dir, "tampered.json");
  fs.writeFileSync(tampered, JSON.stringify({ ...signed, license: { ...signed.license, licensee: { ...signed.license.licensee, company: `${buyer.company} Holdings` } } }, null, 2));
  const bad = verifyLicenseCli(tampered, E.keyringFile);
  expect(bad.status, bad.out).toBe(1);
  expect(bad.out).toMatch(/INVALID/);
  const shipped = verifyLicenseCli(file, null);
  expect(shipped.status, shipped.out).toBe(1);
  expect(shipped.out).toMatch(/not in the keyring/);
});
