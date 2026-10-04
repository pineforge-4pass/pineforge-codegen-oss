"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import { commerce } from "@/lib/commerce-config";
import { findPlan, formatMoney, productName } from "@/lib/commerce";
import { HONEYPOT_FIELD, validateCheckout, type FieldError as FieldErr } from "@/lib/validate";
import { AGREEMENT_IS_DRAFT } from "@/lib/generated/build-info";
import { Field, FieldError, Honeypot } from "./form-field";
import { CONTACT_EMAIL, localePath } from "./paths";

const FIELDS = ["company", "country", "buyerName", "buyerEmail", "reference", "acceptAgreement"] as const;
type FieldName = (typeof FIELDS)[number];

type Query = { tier: string; option: string; canceled: boolean };

export function CheckoutForm() {
  const t = useTranslations("checkout");
  const tc = useTranslations("common");
  const tp = useTranslations("plans");
  const locale = useLocale();
  const [query, setQuery] = useState<Query | null>(null);
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    setQuery({ tier: sp.get("tier") ?? "", option: sp.get("option") ?? "", canceled: sp.get("canceled") === "1" });
  }, []);

  if (!query) return <p className="mt-8">{tc("loading")}</p>;

  const plan = findPlan(commerce, query.tier, query.option);
  if (!plan) {
    return (
      <div data-testid="checkout-invalid" className="mt-8">
        <p className="alert measure">{t("invalid")}</p>
        <p className="mt-4">
          <a className="link inline-flex min-h-11 items-center font-medium" href={localePath(locale, "plans/")}>
            {t("invalidLink")}
          </a>
        </p>
      </div>
    );
  }
  const { tier, option } = plan;

  const labels: Record<FieldName, string> = {
    company: t("company"),
    country: t("country"),
    buyerName: t("buyerName"),
    buyerEmail: t("buyerEmail"),
    reference: t("reference"),
    acceptAgreement: AGREEMENT_IS_DRAFT ? t("acceptDraft") : t("acceptFinal"),
  };

  function message(e: FieldErr): string {
    if (e.field === "acceptAgreement") return t("errors.acceptAgreementRequired");
    if (e.field === "buyerEmail" && e.code === "invalid") return t("errors.buyerEmailInvalid");
    const label = (labels[e.field as FieldName] ?? e.field).replace(/\s*\(.*\)$/, "");
    return t(`errors.${e.code}`, { label });
  }

  function showErrors(list: FieldErr[]) {
    const next: Partial<Record<FieldName, string>> = {};
    let other = false;
    for (const e of list) {
      if ((FIELDS as readonly string[]).includes(e.field)) {
        const f = e.field as FieldName;
        if (!next[f]) next[f] = message(e);
      } else other = true;
    }
    setErrors(next);
    if (other) setFormError(t("errors.generic", { email: CONTACT_EMAIL }));
    const first = FIELDS.find((f) => next[f]);
    if (first) requestAnimationFrame(() => document.getElementById(`field-${first}`)?.focus());
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const fd = new FormData(event.currentTarget);
    const str = (k: string) => String(fd.get(k) ?? "");
    const body = {
      tier: tier.id,
      option: option.id,
      company: str("company"),
      country: str("country"),
      buyerName: str("buyerName"),
      buyerEmail: str("buyerEmail"),
      reference: str("reference"),
      acceptAgreement: fd.get("acceptAgreement") === "on",
      locale,
      [HONEYPOT_FIELD]: str(HONEYPOT_FIELD),
    };
    setFormError(null);
    const checked = validateCheckout(body);
    if (!checked.ok) {
      showErrors(checked.errors);
      return;
    }
    setErrors({});
    setSubmitting(true);
    try {
      const res = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json().catch(() => null)) as { url?: unknown; error?: unknown; fields?: unknown } | null;
      if (res.ok && data && typeof data.url === "string") {
        window.location.assign(data.url);
        return;
      }
      if (res.status === 400 && data?.error === "invalid_fields" && Array.isArray(data.fields)) {
        showErrors(data.fields as FieldErr[]);
      } else if (res.status === 400 && data?.error === "invalid_plan") {
        setFormError(t("errors.invalidPlan"));
      } else if (res.status === 429) {
        setFormError(t("errors.rateLimited"));
      } else if (res.status === 503) {
        setFormError(t("errors.liveDisabled"));
      } else if (res.status === 502) {
        setFormError(t("errors.provider"));
      } else {
        setFormError(t("errors.generic", { email: CONTACT_EMAIL }));
      }
    } catch {
      setFormError(t("errors.network"));
    }
    setSubmitting(false);
  }

  const termsHref = localePath(locale, "terms/#agreement");

  return (
    <div className="mt-10 grid gap-12 lg:grid-cols-12">
      <section
        data-testid="order-summary"
        aria-labelledby="summary-heading"
        className="border-t border-ink pt-5 lg:col-span-5 lg:col-start-8 lg:row-start-1"
      >
        <h2 id="summary-heading" className="subheading">
          {t("summaryHeading")}
        </h2>
        <dl className="mt-4 grid gap-4">
          <div>
            <dt className="small">{t("product")}</dt>
            <dd data-testid="order-product" className="font-medium">
              {productName(commerce, tier, option)}
            </dd>
          </div>
          <div>
            <dt className="small">{t("price")}</dt>
            <dd>
              <span data-testid="order-price" className="mono text-[22px] font-medium">
                {formatMoney(option.annual, commerce.currency)}
              </span>{" "}
              <span className="text-ink-2">{tc("perYear")}</span>
            </dd>
          </div>
          <div>
            <dt className="small">{t("term")}</dt>
            <dd>{t("termValue", { months: commerce.termMonths })}</dd>
          </div>
        </dl>
        <p className="small mt-4">{t("amountNote")}</p>
        {commerce.pricesArePlaceholders ? <p className="small mt-2">{tp("placeholders")}</p> : null}
      </section>

      <div className="lg:col-span-7 lg:row-start-1">
        {query.canceled ? (
          <p role="status" className="note mb-6">
            {t("canceled")}
          </p>
        ) : null}
        <h2 className="subheading">{t("formHeading")}</h2>
        <p className="small measure mt-2">{t("formIntro")}</p>
        <form noValidate onSubmit={onSubmit} className="relative mt-6 grid max-w-xl gap-5">
          <Field name="company" label={labels.company} error={errors.company} autoComplete="organization" required />
          <Field name="country" label={labels.country} error={errors.country} autoComplete="country-name" required />
          <Field name="buyerName" label={labels.buyerName} error={errors.buyerName} autoComplete="name" required />
          <Field
            name="buyerEmail"
            label={labels.buyerEmail}
            error={errors.buyerEmail}
            type="email"
            autoComplete="email"
            required
          />
          <Field name="reference" label={labels.reference} error={errors.reference} />
          <div>
            <div className="flex min-h-11 items-center gap-3">
              <input
                id="field-acceptAgreement"
                type="checkbox"
                name="acceptAgreement"
                aria-invalid={errors.acceptAgreement ? true : undefined}
                aria-describedby={
                  "agreement-note" + (errors.acceptAgreement ? " error-acceptAgreement" : "")
                }
              />
              <label htmlFor="field-acceptAgreement" className="cursor-pointer font-medium">
                {labels.acceptAgreement}
              </label>
            </div>
            <p id="agreement-note" className="small mt-1 pl-8">
              {t.rich(AGREEMENT_IS_DRAFT ? "agreementNoteDraft" : "agreementNoteFinal", {
                link: (chunks) => (
                  <a className="link" href={termsHref}>
                    {chunks}
                  </a>
                ),
              })}
            </p>
            {errors.acceptAgreement ? (
              <FieldError id="error-acceptAgreement" name="acceptAgreement" message={errors.acceptAgreement} />
            ) : null}
          </div>
          <Honeypot label={t("honeypot")} />
          {formError ? (
            <div data-testid="form-error" role="alert" className="alert">
              {formError}
            </div>
          ) : null}
          <div>
            <button type="submit" data-testid="checkout-submit" className="btn-action" disabled={submitting}>
              {submitting ? t("submitting") : t("submit")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
