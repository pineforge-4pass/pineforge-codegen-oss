// 1. Decision guide ("Do I need one?") on the home page: answer paths -> outcome + verbatim LICENSE quotes.
import { test, expect } from "@playwright/test";
import { LICENSE_URL, licenseText, normalizeText } from "../support/e2e.mjs";

const NO_USES = { othersCapital: false, organization: false, embedding: false, hosted: false };
const USE_QS = ["othersCapital", "organization", "embedding", "hosted"];
const PT_QS = ["naturalPerson", "ownAccount", "ownCapital"];

// The questions the guide must ask for a set of answers (contract "Decision guide"). "noncommercialOrg"
// is asked right after "organization" when the answer to it is Yes.
function expectedPath(a) {
  const asked = a.organization ? ["othersCapital", "organization", "noncommercialOrg", "embedding", "hosted"] : [...USE_QS];
  if (USE_QS.some((q) => a[q])) return asked;
  for (const q of PT_QS) {
    asked.push(q);
    if (!a[q]) return [...asked, "noncommercial"];
  }
  return asked;
}

const PATHS = [
  {
    name: "work for an organization -> Team",
    answers: { ...NO_USES, organization: true, noncommercialOrg: false },
    outcome: "commercial-team",
    quotes: ["use2", "usesAreCommercial", "commercialUse"],
  },
  {
    name: "work for a noncommercial organization -> ask first",
    answers: { ...NO_USES, organization: true, noncommercialOrg: true },
    outcome: "ask-first",
    quotes: ["noncommercialOrgs", "use2", "usesAreCommercial", "supplementalControl"],
  },
  {
    name: "others' capital for a fund -> Fund",
    answers: { ...NO_USES, othersCapital: true, organization: true, noncommercialOrg: false },
    outcome: "commercial-fund",
    quotes: ["use1", "use2", "usesAreCommercial", "commercialUse"],
  },
  {
    name: "hosted service -> OEM",
    answers: { ...NO_USES, hosted: true },
    outcome: "commercial-oem",
    quotes: ["use4", "usesAreCommercial", "commercialUse"],
  },
  {
    name: "others' capital and embedding -> quote",
    answers: { ...NO_USES, othersCapital: true, embedding: true },
    outcome: "commercial-quote",
    quotes: ["use1", "use3", "usesAreCommercial", "commercialUse"],
  },
  {
    name: "personal trading -> no commercial license",
    answers: { ...NO_USES, naturalPerson: true, ownAccount: true, ownCapital: true },
    outcome: "personal-trading",
    quotes: ["personalTradingGrant", "personalTradingDef", "personalTradingA", "personalTradingB"],
  },
  {
    name: "noncommercial organization -> permitted purpose",
    answers: { ...NO_USES, naturalPerson: false, noncommercial: true },
    outcome: "permitted-noncommercial",
    quotes: ["noncommercialPurposes", "personalUses"],
  },
  {
    name: "own account but not own capital, not noncommercial -> talk to us",
    answers: { ...NO_USES, naturalPerson: true, ownAccount: true, ownCapital: false, noncommercial: false },
    outcome: "commercial-other",
    quotes: ["commercialUse"],
  },
];

async function openGuide(page) {
  await page.goto("/en/");
  await expect(page.getByTestId("decision-guide")).toBeVisible();
  const guide = page.getByTestId("guide-interactive");
  await expect(guide).toBeVisible();
  await expect(page.getByTestId("guide-static")).toBeHidden();
  return guide;
}

const result = (page) => page.getByTestId("decision-guide").getByTestId("guide-result").filter({ visible: true });

/** Answers the next unanswered question until no question is left unanswered. Returns the questions seen, in order. */
async function answerAll(guide, answers) {
  for (let step = 0; step < 12; step += 1) {
    const sets = guide.locator("fieldset[data-question]");
    const n = await sets.count();
    let answered = false;
    for (let i = 0; i < n; i += 1) {
      const set = sets.nth(i);
      const q = await set.getAttribute("data-question");
      const yes = set.getByRole("radio", { name: "Yes", exact: true });
      const no = set.getByRole("radio", { name: "No", exact: true });
      if ((await yes.isChecked()) || (await no.isChecked())) continue;
      expect(Object.keys(answers), `the guide asked "${q}", which this path does not answer`).toContain(q);
      await (answers[q] ? yes : no).check();
      answered = true;
      break;
    }
    if (!answered) break;
  }
  return guide.locator("fieldset[data-question]").evaluateAll((els) => els.map((e) => e.getAttribute("data-question")));
}

test.describe("decision guide", () => {
  const license = licenseText();

  for (const p of PATHS) {
    test(`${p.name}`, async ({ page }) => {
      const guide = await openGuide(page);
      const asked = await answerAll(guide, p.answers);
      expect(asked).toEqual(expectedPath(p.answers));
      for (const set of await guide.locator("fieldset[data-question]").all()) {
        await expect(set.locator("legend")).not.toBeEmpty();
      }

      const res = result(page);
      await expect(res).toHaveCount(1);
      await expect(res).toHaveAttribute("data-outcome", p.outcome);
      const quotes = res.locator('blockquote[data-testid="license-quote"]');
      const ids = await quotes.evaluateAll((els) => els.map((e) => e.getAttribute("data-quote")));
      expect([...ids].sort()).toEqual([...p.quotes].sort());
      if (p.quotes.includes("supplementalControl")) {
        await expect(quotes.and(res.locator('[data-quote="supplementalControl"]'))).toHaveCount(1);
        expect(normalizeText(await res.locator('blockquote[data-quote="supplementalControl"]').innerText())).toContain(
          'In case of any conflict, the supplemental sections ("Additional Permission" and "Commercial Use") control over the base license.',
        );
      }
      for (const q of await quotes.all()) {
        const text = normalizeText(await q.innerText());
        expect(text.length).toBeGreaterThan(20);
        expect(license, `quote "${await q.getAttribute("data-quote")}" must appear verbatim in LICENSE`).toContain(text);
      }
      await expect(res.locator(`a[href="${LICENSE_URL}"]`).first()).toBeVisible();
    });
  }

  test("answers stay changeable, and Start over resets", async ({ page }) => {
    const guide = await openGuide(page);
    await answerAll(guide, PATHS[0].answers);
    await expect(result(page)).toHaveAttribute("data-outcome", "commercial-team");

    // Change an earlier answer: organization -> No drops the noncommercial-organization question and reveals the
    // Personal Trading test, hosted -> Yes leads to OEM.
    const org = guide.locator('fieldset[data-question="organization"]');
    await org.getByRole("radio", { name: "No", exact: true }).check();
    await expect(guide.locator('fieldset[data-question="noncommercialOrg"]')).toHaveCount(0);
    await expect(guide.locator('fieldset[data-question="naturalPerson"]')).toBeVisible();
    await guide.locator('fieldset[data-question="hosted"]').getByRole("radio", { name: "Yes", exact: true }).check();
    await expect(result(page)).toHaveAttribute("data-outcome", "commercial-oem");
    await expect(guide.locator("fieldset[data-question]")).toHaveCount(4);

    await page.getByRole("button", { name: "Start over" }).click();
    await expect(result(page)).toHaveCount(0);
    const first = guide.locator("fieldset[data-question]").first();
    await expect(first).toHaveAttribute("data-question", "othersCapital");
    await expect(first.getByRole("radio", { name: "Yes", exact: true })).not.toBeChecked();
    await expect(first.getByRole("radio", { name: "No", exact: true })).not.toBeChecked();
  });

  test("states it is not legal advice and links the LICENSE", async ({ page }) => {
    await openGuide(page);
    await expect(page.getByTestId("decision-guide").getByText(/not legal advice/i).first()).toBeVisible();
    await expect(page.getByTestId("test-mode-notice")).toBeVisible();
    await expect(page.locator('a[href="#main"]').first()).toBeAttached();
    await expect(page.locator("main#main")).toHaveCount(1);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });
});

test.describe("decision guide without JavaScript", () => {
  test.use({ javaScriptEnabled: false });
  test("the static fallback is readable", async ({ page }) => {
    await page.goto("/en/");
    const fallback = page.getByTestId("guide-static");
    await expect(fallback).toBeVisible();
    expect((await fallback.innerText()).trim().length).toBeGreaterThan(200);
    await expect(page.getByTestId("guide-interactive")).toBeHidden();
  });
});
