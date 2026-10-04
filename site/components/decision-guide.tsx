"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { LICENSE_URL } from "@/content/license-quotes";
import { decide, path, QUESTION_ORDER, type Answers, type QuestionId } from "@/lib/decide";
import { LicenseQuote } from "./license-quote";
import { outcomeHref } from "./guide-links";

/**
 * Progressive enhancement: the server renders the static guide; once this
 * component runs it hides that copy and shows the interactive questionnaire.
 */
export function DecisionGuide({ staticFallback, locale }: { staticFallback: ReactNode; locale: string }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return (
    <>
      <div data-testid="guide-static" hidden={mounted}>
        {staticFallback}
      </div>
      {mounted ? <InteractiveGuide locale={locale} /> : null}
    </>
  );
}

function prune(answers: Answers): Answers {
  const keep = new Set(path(answers));
  const out: Answers = {};
  for (const q of QUESTION_ORDER) {
    if (keep.has(q) && answers[q] !== undefined) out[q] = answers[q];
  }
  return out;
}

function InteractiveGuide({ locale }: { locale: string }) {
  const t = useTranslations("guide");
  const [answers, setAnswers] = useState<Answers>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const asked = path(answers);
  const outcome = decide(answers);

  function answer(q: QuestionId, value: boolean) {
    setAnswers((prev) => prune({ ...prev, [q]: value }));
  }

  function startOver() {
    setAnswers({});
    requestAnimationFrame(() => {
      rootRef.current?.querySelector<HTMLInputElement>("input[type=radio]")?.focus();
    });
  }

  const href = outcome ? outcomeHref(locale, outcome) : null;

  return (
    <div data-testid="guide-interactive" ref={rootRef} className="mt-8">
      <ol className="grid gap-5">
        {asked.map((q, i) => {
          // Numbered by the questions actually asked, so a skipped follow-up leaves no gap.
          const n = i + 1;
          const hintId = `guide-hint-${q}`;
          return (
            <li key={q}>
              <fieldset data-question={q} aria-describedby={hintId} className="measure border-t border-hair pt-4">
                <legend className="float-left w-full font-medium">
                  <span className="mono block text-[13px] font-normal text-ink-2">{t("questionNumber", { n })}</span>
                  <span className="mt-1 block text-[17px] leading-[1.45]">{t(`questions.${q}.text`)}</span>
                </legend>
                <p id={hintId} className="small clear-both pt-1">
                  {t(`questions.${q}.hint`)}
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  <label className="choice">
                    <input
                      type="radio"
                      name={`guide-${q}`}
                      value="yes"
                      checked={answers[q] === true}
                      onChange={() => answer(q, true)}
                    />
                    {t("yes")}
                  </label>
                  <label className="choice">
                    <input
                      type="radio"
                      name={`guide-${q}`}
                      value="no"
                      checked={answers[q] === false}
                      onChange={() => answer(q, false)}
                    />
                    {t("no")}
                  </label>
                </div>
              </fieldset>
            </li>
          );
        })}
      </ol>

      {!outcome && Object.keys(answers).length > 0 ? (
        <div className="mt-6">
          <button type="button" className="btn" onClick={startOver}>
            {t("startOver")}
          </button>
        </div>
      ) : null}

      <div aria-live="polite">
        {outcome ? (
          <div
            data-testid="guide-result"
            data-outcome={outcome.id}
            className="measure mt-10 border-t-2 border-ink pt-6"
          >
            <p className="mono text-[13px] text-ink-2">{t("resultHeading")}</p>
            <h3 className="subheading mt-1">{t(`outcomes.${outcome.id}.title`)}</h3>
            <p className="mt-3">{t(`outcomes.${outcome.id}.body`)}</p>
            <div className="mt-5 grid gap-4">
              {outcome.quotes.map((q) => (
                <LicenseQuote key={q} id={q} />
              ))}
            </div>
            <p className="mt-3">
              <a className="link inline-flex min-h-11 items-center" href={LICENSE_URL}>
                {t("licenseLink")}
              </a>
            </p>
            <div className="mt-5 flex flex-wrap items-center gap-4">
              {href ? (
                <a className="btn-action" href={href}>
                  {t(`outcomes.${outcome.id}.action`)}
                </a>
              ) : null}
              <button type="button" className="btn" onClick={startOver}>
                {t("startOver")}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
