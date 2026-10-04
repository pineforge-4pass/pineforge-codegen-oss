import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { commerce } from "@/lib/commerce-config";

type Props = { params: Promise<{ locale: string }> };

const ITEMS = [
  "who",
  "personal",
  "seats",
  "aum",
  "upgrade",
  "renewal",
  "invoice",
  "refund",
  "verify",
  "data",
  "advice",
  "preview",
] as const;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "faq" });
  return { title: t("metaTitle") };
}

export default async function FaqPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "faq" });
  const items = ITEMS.filter((k) => k !== "preview" || commerce.pricesArePlaceholders);
  return (
    <div className="wrap pt-14 sm:pt-20">
      <h1 className="display">{t("title")}</h1>
      <div className="mt-12 grid gap-10">
        {items.map((k) => (
          <section key={k} aria-labelledby={`faq-${k}`} className="measure border-t border-hair pt-5">
            <h2 id={`faq-${k}`} className="subheading">
              {t(`items.${k}.q`)}
            </h2>
            <p className="mt-3">{t(`items.${k}.a`)}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
