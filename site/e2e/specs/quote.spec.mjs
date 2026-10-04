// 7. Quote form ("talk to us"): the request is accepted and emailed to enterprise@pineforge.dev with
// reply-to the requester; a filled honeypot is answered 200 and sends nothing.
import { test, expect } from "@playwright/test";
import { E, NOTIFY_TO, listEmails, makeBuyer, postJson, recipients, waitForEmails } from "../support/e2e.mjs";

test("submitting the quote form emails enterprise@pineforge.dev with reply-to the requester", async ({ page, request }) => {
  const who = makeBuyer("quote", test.info());
  await page.goto("/en/contact/");
  await expect(page.locator('a[href^="mailto:enterprise@pineforge.dev"]').first()).toBeVisible();
  const form = page.locator("#quote");
  await expect(form).toBeVisible();
  await form.getByLabel("Name", { exact: true }).fill("Grace Requester");
  await form.getByLabel("Email", { exact: true }).fill(who.email);
  await form.getByLabel("Company", { exact: true }).fill(who.company);
  await form.getByLabel("Use case", { exact: true }).selectOption("fund");
  await form.getByLabel("Seats (estimate)", { exact: true }).fill("40");
  await form.getByLabel("Assets under management (estimate)", { exact: true }).fill("USD 750M");
  await form.getByLabel("Deployment: products and end users (estimate)", { exact: true }).fill("Internal use only");
  await form.getByLabel("Message", { exact: true }).fill(`E2E quote request ${who.tag}: AUM above the largest band.`);
  await page.getByTestId("quote-submit").click();
  const ok = page.getByTestId("quote-success");
  await expect(ok).toBeVisible();
  await expect(ok).toHaveAttribute("role", "status");

  const [mail] = await waitForEmails(request, (m) => recipients(m).includes(NOTIFY_TO) && String(m.subject).includes(who.company));
  expect(mail.subject).toBe(`Quote request: ${who.company} (fund)`);
  expect([mail.reply_to].flat().map((r) => String(r).toLowerCase())).toContain(who.email);
  expect(mail.text).toContain(who.tag);
  expect(mail.text).toContain("USD 750M");
});

test("quote API: honeypot answered 200 and nothing sent; invalid fields answered 400", async ({ request }) => {
  const who = makeBuyer("quote-trap", test.info());
  const body = {
    name: "Bot",
    email: who.email,
    company: who.company,
    useCase: "team",
    seats: "5",
    aum: "",
    deployment: "",
    message: "buy now",
    locale: "en",
    pf_hp: "http://spam.example",
  };
  const trap = await postJson(request, `${E.base}/api/quote`, body);
  expect(trap.status, trap.text).toBe(200);
  expect(trap.body).toEqual({ ok: true });

  const invalid = await postJson(request, `${E.base}/api/quote`, { ...body, pf_hp: "", email: "not-an-email" });
  expect(invalid.status, invalid.text).toBe(400);
  expect(invalid.body.error).toBe("invalid_fields");
  expect(invalid.body.fields.map((f) => f.field)).toContain("email");

  await new Promise((r) => setTimeout(r, 2000));
  expect((await listEmails(request)).filter((m) => JSON.stringify(m).includes(who.company))).toEqual([]);
});
