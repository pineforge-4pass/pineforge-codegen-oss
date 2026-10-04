import { getTranslations } from "next-intl/server";
import { LICENSE_URL } from "@/content/license-quotes";
import { decide, QUESTION_ORDER, type Answers, type Outcome, type QuestionId } from "@/lib/decide";
import { LicenseQuote } from "./license-quote";
import { outcomeHref } from "./guide-links";

// One set of answers per outcome, so the static fallback lists every result
// with the quotations lib/decide.ts attaches to it.
const EXAMPLES: Answers[] = [
  { othersCapital: false, organization: true, noncommercialOrg: false, embedding: false, hosted: false },
  { othersCapital: true, organization: true, noncommercialOrg: false, embedding: false, hosted: false },
  { othersCapital: false, organization: false, embedding: true, hosted: false },
  { othersCapital: true, organization: false, embedding: true, hosted: false },
  { othersCapital: false, organization: true, noncommercialOrg: true, embedding: false, hosted: false },
  {
    othersCapital: false,
    organization: false,
    embedding: false,
    hosted: false,
    naturalPerson: true,
    ownAccount: true,
    ownCapital: true,
  },
  { othersCapital: false, organization: false, embedding: false, hosted: false, naturalPerson: false, noncommercial: true },
  { othersCapital: false, organization: false, embedding: false, hosted: false, naturalPerson: false, noncommercial: false },
];

/**
 * The guide without JavaScript: every question, the rules that join them,
 * and every possible result with its LICENSE quotations.
 */
export async function GuideStatic({ locale }: { locale: string }) {
  const t = await getTranslations({ locale, namespace: "guide" });
  // Question numbers follow QUESTION_ORDER, as in the list below and the interactive guide.
  const n = (q: QuestionId) => QUESTION_ORDER.indexOf(q) + 1;
  const numbers = {
    use1: n("othersCapital"),
    use2: n("organization"),
    use3: n("embedding"),
    use4: n("hosted"),
    org: n("noncommercialOrg"),
    pt1: n("naturalPerson"),
    pt3: n("ownCapital"),
    nc: n("noncommercial"),
  };
  const seen = new Set<string>();
  const outcomes: Outcome[] = [];
  for (const answers of EXAMPLES) {
    const o = decide(answers);
    if (o && !seen.has(o.id)) {
      seen.add(o.id);
      outcomes.push(o);
    }
  }
  return (
    <div className="mt-8">
      <h3 className="subheading">{t("static.heading")}</h3>
      <ol className="measure mt-3 grid list-decimal gap-2 pl-6">
        <li>{t("static.rule1", numbers)}</li>
        <li>{t("static.ruleOrg", numbers)}</li>
        <li>{t("static.rule2", numbers)}</li>
        <li>{t("static.rule3", numbers)}</li>
      </ol>

      <h3 className="subheading mt-10">{t("static.questionsHeading")}</h3>
      <ol className="measure mt-3 grid gap-4">
        {QUESTION_ORDER.map((q, i) => (
          <li key={q} className="grid grid-cols-[2rem_1fr] gap-2">
            <span className="mono text-ink-2">{i + 1}.</span>
            <span>
              <span className="block font-medium">{t(`questions.${q}.text`)}</span>
              <span className="small block">{t(`questions.${q}.hint`)}</span>
            </span>
          </li>
        ))}
      </ol>

      <h3 className="subheading mt-10">{t("static.outcomesHeading")}</h3>
      <div className="mt-4 grid gap-10">
        {outcomes.map((o) => {
          const href = outcomeHref(locale, o);
          return (
            <section key={o.id} className="measure border-t border-hair pt-5">
              <h4 className="text-[18px] font-semibold">{t(`outcomes.${o.id}.title`)}</h4>
              <p className="mt-2">{t(`outcomes.${o.id}.body`)}</p>
              <div className="mt-4 grid gap-4">
                {o.quotes.map((q) => (
                  <LicenseQuote key={q} id={q} hooks={false} />
                ))}
              </div>
              <p className="mt-3 flex flex-wrap gap-x-6">
                <a className="link inline-flex min-h-11 items-center" href={LICENSE_URL}>
                  {t("licenseLink")}
                </a>
                {href ? (
                  <a className="link inline-flex min-h-11 items-center font-medium" href={href}>
                    {t(`outcomes.${o.id}.action`)}
                  </a>
                ) : null}
              </p>
            </section>
          );
        })}
      </div>
    </div>
  );
}
