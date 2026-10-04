import { getTranslations } from "next-intl/server";
import { commerce } from "@/lib/commerce-config";
import { isPreview } from "@/lib/go-live";
import { localePath } from "./paths";
import { SiteNav } from "./site-nav";

export async function SiteHeader({ locale }: { locale: string }) {
  const t = await getTranslations({ locale, namespace: "common" });
  const items = [
    { href: localePath(locale), label: t("nav.home") },
    { href: localePath(locale, "plans/"), label: t("nav.plans") },
    { href: localePath(locale, "verify/"), label: t("nav.verify") },
    { href: localePath(locale, "faq/"), label: t("nav.faq") },
    { href: localePath(locale, "terms/"), label: t("nav.terms") },
    { href: localePath(locale, "contact/"), label: t("nav.contact") },
  ];
  return (
    <header className="border-b border-hair">
      {isPreview() ? (
        <div className="border-b border-hair bg-field">
          <p data-testid="test-mode-notice" className="wrap py-2 text-[14px] text-ink-2">
            {commerce.pricesArePlaceholders ? t("testModeNoticePlaceholders") : t("testModeNotice")}
          </p>
        </div>
      ) : null}
      <div className="wrap flex flex-wrap items-center justify-between gap-x-10 gap-y-1 py-3">
        <a href={localePath(locale)} className="inline-flex min-h-11 items-center no-underline">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[17px] font-semibold tracking-[-0.01em]">{t("siteName")}</span>
            <span className="text-[15px] text-ink-2">{t("siteSection")}</span>
          </span>
        </a>
        <SiteNav items={items} label={t("nav.label")} />
      </div>
    </header>
  );
}
