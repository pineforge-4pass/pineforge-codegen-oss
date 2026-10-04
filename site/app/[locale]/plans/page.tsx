import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { commerce } from "@/lib/commerce-config";
import { formatMoney, scopeSummary } from "@/lib/commerce";
import { TIER_COVERS } from "@/lib/decide";
import { localePath } from "@/components/paths";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "plans" });
  return { title: t("metaTitle") };
}

export default async function PlansPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "plans" });
  const c = await getTranslations({ locale, namespace: "common" });
  const quoteHref = localePath(locale, "contact/#quote");

  return (
    <div className="wrap pt-14 sm:pt-20">
      <h1 className="display">{t("title")}</h1>
      <p className="lead measure mt-6">{t("lead")}</p>
      {commerce.pricesArePlaceholders ? <p className="note measure mt-6">{t("placeholders")}</p> : null}

      <div className="mt-14 grid gap-16">
        {commerce.tiers.map((tier) => {
          const uses = TIER_COVERS.find((x) => x.tier === tier.id)?.uses ?? [];
          const tierName = c(`tiers.${tier.id}`);
          return (
            <section
              key={tier.id}
              id={`tier-${tier.id}`}
              data-testid={`tier-${tier.id}`}
              aria-labelledby={`tier-${tier.id}-name`}
              className="ledger-group"
            >
              <div className="grid gap-x-10 gap-y-3 lg:grid-cols-12">
                <div className="lg:col-span-4">
                  <h2 id={`tier-${tier.id}-name`} className="heading">
                    {tierName}
                  </h2>
                  <p className="mono mt-2 text-[14px] text-ink-2">
                    {t("covers", { uses: uses.map((u) => `(${u})`).join(", ") })}
                  </p>
                </div>
                <dl className="grid gap-3 lg:col-span-8">
                  <div>
                    <dt className="font-semibold">{t("permitsLabel")}</dt>
                    <dd className="measure">{t(`tiers.${tier.id}.permits`)}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">{t("notCoveredLabel")}</dt>
                    <dd className="measure">{t(`tiers.${tier.id}.notCovered`)}</dd>
                  </div>
                </dl>
              </div>

              <ul className="mt-8 grid gap-0">
                {tier.options.map((option) => {
                  const scope = scopeSummary(tier, option);
                  return (
                    <li
                      key={option.id}
                      data-testid={`option-${tier.id}-${option.id}`}
                      className="ledger-row border-t border-hair py-5"
                    >
                      <h3 className="ledger-name">{scope}</h3>
                      <p className="ledger-price">
                        <span className="whitespace-nowrap">{formatMoney(option.annual, commerce.currency)}</span>{" "}
                        <span className="block font-sans text-[14px] font-normal text-ink-2 lg:inline">{c("perYear")}</span>
                      </p>
                      <div className="ledger-terms flex flex-wrap items-baseline justify-between gap-x-6">
                        <span>{t("termLine", { months: commerce.termMonths })}</span>
                        <a
                          data-testid={`buy-${tier.id}-${option.id}`}
                          href={localePath(locale, `checkout/?tier=${tier.id}&option=${option.id}`)}
                          className="link inline-flex min-h-11 items-center text-[17px] font-semibold text-ink"
                          aria-label={t("buyLabel", { tier: tierName, scope })}
                        >
                          {t("buy")}
                        </a>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="border-t border-hair pt-4 text-ink-2">
                {t("above", { quoteAbove: tier.quoteAbove })}{" "}
                <a className="link inline-flex min-h-11 items-center" href={quoteHref}>
                  {t("aboveLink")}
                </a>
              </p>
            </section>
          );
        })}

        <section aria-labelledby="no-fit" className="ledger-group">
          <div className="ledger-row">
            <h2 id="no-fit" className="ledger-name">
              {t("quoteHeading")}
            </h2>
            <div className="ledger-terms lg:col-start-8">
              <p>{t("quoteBody")}</p>
              <p className="mt-1">
                <a
                  data-testid="quote-link"
                  className="link inline-flex min-h-11 items-center text-[17px] font-semibold text-ink"
                  href={quoteHref}
                >
                  {t("quoteLink")}
                </a>
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
