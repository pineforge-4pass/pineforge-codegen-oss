import { readFile } from "node:fs/promises";
import path from "node:path";
import type { Metadata } from "next";
import { marked } from "marked";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { commerce } from "@/lib/commerce-config";
import { AGREEMENT_IS_DRAFT, AGREEMENT_SHA256, AGREEMENT_VERSION } from "@/lib/generated/build-info";
import { CONTACT_EMAIL } from "@/components/paths";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "terms" });
  return { title: t("metaTitle") };
}

/** The agreement as HTML, rendered at build time; its headings sit under the page's h2. */
async function agreementHtml(): Promise<string> {
  const md = await readFile(path.join(process.cwd(), "legal", "commercial-license-agreement.md"), "utf8");
  const html = await marked.parse(md, { gfm: true });
  return html.replace(/<(\/?)h([1-6])(\s[^>]*)?>/g, (_m, slash: string, level: string, rest?: string) => {
    const n = Math.min(6, Number(level) + 2);
    return `<${slash}h${n}${rest ?? ""}>`;
  });
}

export default async function TermsPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "terms" });
  const html = await agreementHtml();
  const seller = commerce.seller.legalName;
  return (
    <div className="wrap pt-14 sm:pt-20">
      <h1 className="display">{t("title")}</h1>

      <section aria-labelledby="sale-summary" className="mt-12">
        <h2 id="sale-summary" className="heading">
          {t("summaryHeading")}
        </h2>
        <ul className="measure mt-5 grid gap-3">
          <li>{seller ? t("summary.seller", { seller }) : t("summary.sellerPending")}</li>
          <li>{t("summary.what", { months: commerce.termMonths })}</li>
          <li>{t("summary.payment")}</li>
          <li>{t("summary.delivery")}</li>
          <li>{t("summary.refunds")}</li>
          {commerce.pricesArePlaceholders ? <li>{t("summary.preview")}</li> : null}
          <li>{t("summary.control", { email: CONTACT_EMAIL })}</li>
        </ul>
      </section>

      <section id="agreement" aria-labelledby="agreement-heading" className="mt-16 border-t border-ink pt-8">
        <h2 id="agreement-heading" className="heading">
          {t("agreementHeading")}
        </h2>
        {AGREEMENT_IS_DRAFT ? (
          <p data-testid="agreement-draft-banner" className="alert measure mt-5">
            {t("draftBanner")}
          </p>
        ) : null}
        <dl className="mt-5 grid gap-2 text-[14px] text-ink-2">
          <div className="flex flex-wrap gap-x-3">
            <dt>{t("version")}</dt>
            <dd className="mono text-ink">{AGREEMENT_VERSION}</dd>
          </div>
          <div className="flex flex-wrap gap-x-3">
            <dt>{t("sha256")}</dt>
            <dd className="mono break-all text-ink">{AGREEMENT_SHA256}</dd>
          </div>
        </dl>
        <article data-testid="agreement" className="prose-doc mt-6" dangerouslySetInnerHTML={{ __html: html }} />
      </section>
    </div>
  );
}
