import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { QuoteForm } from "@/components/quote-form";
import { CONTACT_EMAIL } from "@/components/paths";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "contact" });
  return { title: t("metaTitle") };
}

export default async function ContactPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "contact" });
  return (
    <div className="wrap pt-14 sm:pt-20">
      <h1 className="display">{t("title")}</h1>
      <p className="lead measure mt-6">{t("lead", { email: CONTACT_EMAIL })}</p>
      <p className="mt-4">
        <a className="link inline-flex min-h-11 items-center font-medium" href={`mailto:${CONTACT_EMAIL}`}>
          {t("emailLink")}
        </a>
      </p>

      <section id="quote" aria-labelledby="quote-heading" className="mt-14 border-t border-hair pt-8">
        <h2 id="quote-heading" className="heading">
          {t("quoteHeading")}
        </h2>
        <p className="measure mt-3">{t("quoteIntro")}</p>
        <QuoteForm />
      </section>
    </div>
  );
}
