import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { DecisionGuide } from "@/components/decision-guide";
import { GuideStatic } from "@/components/guide-static";
import { CONTACT_EMAIL, localePath } from "@/components/paths";
import { LICENSE_URL } from "@/content/license-quotes";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "home" });
  return { title: t("metaTitle") };
}

export default async function HomePage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "home" });
  const g = await getTranslations({ locale, namespace: "guide" });
  const tiers = await getTranslations({ locale, namespace: "common.tiers" });

  return (
    <>
      <section className="wrap pt-14 sm:pt-20" aria-labelledby="home-title">
        <h1 id="home-title" className="display max-w-[22ch]">
          {t("title")}
        </h1>
        <p className="lead measure mt-6">{t("lead")}</p>
      </section>

      <section className="wrap mt-16" aria-labelledby="covers">
        <h2 id="covers" className="heading">
          {t("coversHeading")}
        </h2>
        <p className="measure mt-4">{t("coversIntro")}</p>
        <ol className="measure mt-6 grid gap-4">
          {([1, 2, 3, 4] as const).map((n) => (
            <li key={n} className="grid grid-cols-[4.75rem_1fr] gap-3 border-t border-hair pt-3">
              <span className="mono text-ink-2">{t("useLabel", { n })}</span>
              <span>{t(`uses.${n}`)}</span>
            </li>
          ))}
        </ol>
        <p className="measure mt-8">
          {t("plansIntro", { team: tiers("team"), fund: tiers("fund"), oem: tiers("oem") })}
        </p>
        <p className="mt-3">
          <a className="link inline-flex min-h-11 items-center font-medium" href={localePath(locale, "plans/")}>
            {t("seePlans")}
          </a>
        </p>
      </section>

      <section id="guide" className="wrap mt-20" aria-labelledby="guide-heading" data-testid="decision-guide">
        <div className="border-t border-ink pt-10">
          <h2 id="guide-heading" className="heading">
            {g("heading")}
          </h2>
          <p className="measure mt-4">{g("intro")}</p>
          <p className="note measure mt-5">
            {g.rich("disclaimer", {
              license: (chunks) => (
                <a className="link" href={LICENSE_URL}>
                  {chunks}
                </a>
              ),
              email: (chunks) => (
                <a className="link" href={`mailto:${CONTACT_EMAIL}`}>
                  {chunks}
                </a>
              ),
            })}
          </p>
          <DecisionGuide locale={locale} staticFallback={<GuideStatic locale={locale} />} />
        </div>
      </section>

      <section className="wrap mt-20" aria-labelledby="not-covered">
        <h2 id="not-covered" className="heading">
          {t("notCoveredHeading")}
        </h2>
        <p className="measure mt-4">{t("notCovered")}</p>
      </section>
    </>
  );
}
