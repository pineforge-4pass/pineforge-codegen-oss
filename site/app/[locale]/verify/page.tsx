import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { VerifyPanel } from "@/components/verify-panel";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "verify" });
  return { title: t("metaTitle") };
}

export default async function VerifyPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "verify" });
  return (
    <div className="wrap pt-14 sm:pt-20">
      <h1 className="display">{t("title")}</h1>
      <p className="lead measure mt-6">{t("lead")}</p>
      <VerifyPanel />
    </div>
  );
}
