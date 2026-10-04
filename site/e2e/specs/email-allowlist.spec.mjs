// 14. Test-mode buyer emails go only to TEST_EMAIL_ALLOWLIST (run.mjs: "@example.com"). A buyer at another
// domain still gets the license; the sink holds no email to them, and the sale notice still goes out.
import { test, expect } from "@playwright/test";
import { NOTIFY_TO, listEmails, makeBuyer, purchase, recipients, waitForEmails } from "../support/e2e.mjs";

test("a test-mode buyer outside the allow-list gets the license but no email", async ({ page, request }) => {
  const buyer = makeBuyer("allowlist", test.info());
  buyer.email = `${buyer.tag}@not-allowlisted.example`.toLowerCase();
  const { licenseId } = await purchase(page, { tier: "team", option: "seats-5", buyer, fromPlans: false });

  const [notice] = await waitForEmails(request, (m) => recipients(m).includes(NOTIFY_TO) && String(m.subject).includes(licenseId));
  expect(notice.subject).toContain(buyer.company);

  // The notice is sent after the buyer's email would have been; allow a late one to land anyway.
  await new Promise((r) => setTimeout(r, 3000));
  const toBuyer = (await listEmails(request)).filter((m) => recipients(m).includes(buyer.email) || (m.cc && JSON.stringify(m.cc).toLowerCase().includes(buyer.email)));
  expect(toBuyer).toEqual([]);
  expect(recipients(notice)).not.toContain(buyer.email);
});
