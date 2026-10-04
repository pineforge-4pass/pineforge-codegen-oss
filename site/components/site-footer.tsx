import { getTranslations } from "next-intl/server";
import { LICENSE_URL } from "@/content/license-quotes";
import { CONTACT_EMAIL, REPO_URL } from "./paths";

export async function SiteFooter({ locale }: { locale: string }) {
  const t = await getTranslations({ locale, namespace: "common" });
  const link = "link inline-flex min-h-11 items-center";
  return (
    <footer className="mt-24 border-t border-hair">
      <div className="wrap grid gap-2 py-10 text-[15px] text-ink-2">
        <p className="measure">{t("footer.notAdvice")}</p>
        <ul className="flex flex-wrap gap-x-7" aria-label={t("footer.label")}>
          <li>
            <a className={link} href={LICENSE_URL}>
              {t("footer.license")}
            </a>
          </li>
          <li>
            <a className={link} href={REPO_URL}>
              {t("footer.repo")}
            </a>
          </li>
          <li>
            <a className={link} href={`mailto:${CONTACT_EMAIL}`}>
              {CONTACT_EMAIL}
            </a>
          </li>
        </ul>
      </div>
    </footer>
  );
}
