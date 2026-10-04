"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import { HONEYPOT_FIELD, USE_CASES, validateQuote, type FieldError as FieldErr } from "@/lib/validate";
import { Field, Honeypot } from "./form-field";
import { CONTACT_EMAIL } from "./paths";

const FIELDS = ["name", "email", "company", "useCase", "seats", "aum", "deployment", "message"] as const;
type FieldName = (typeof FIELDS)[number];

export function QuoteForm() {
  const t = useTranslations("contact");
  const locale = useLocale();
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [initialUse, setInitialUse] = useState<string>("");

  useEffect(() => {
    const use = new URLSearchParams(window.location.search).get("use") ?? "";
    if ((USE_CASES as readonly string[]).includes(use)) setInitialUse(use);
  }, []);

  const labels: Record<FieldName, string> = {
    name: t("name"),
    email: t("email"),
    company: t("company"),
    useCase: t("useCase"),
    seats: t("seats"),
    aum: t("aum"),
    deployment: t("deployment"),
    message: t("message"),
  };

  function message(e: FieldErr): string {
    if (e.field === "email" && e.code === "invalid") return t("errors.emailInvalid");
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
      name: str("name"),
      email: str("email"),
      company: str("company"),
      useCase: str("useCase"),
      seats: str("seats"),
      aum: str("aum"),
      deployment: str("deployment"),
      message: str("message"),
      locale,
      [HONEYPOT_FIELD]: str(HONEYPOT_FIELD),
    };
    setFormError(null);
    const checked = validateQuote(body);
    if (!checked.ok) {
      showErrors(checked.errors);
      return;
    }
    setErrors({});
    setSubmitting(true);
    try {
      const res = await fetch("/api/quote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json().catch(() => null)) as { ok?: unknown; error?: unknown; fields?: unknown } | null;
      if (res.ok && data?.ok === true) {
        setSent(true);
      } else if (res.status === 400 && data?.error === "invalid_fields" && Array.isArray(data.fields)) {
        showErrors(data.fields as FieldErr[]);
      } else if (res.status === 429) {
        setFormError(t("errors.rateLimited"));
      } else {
        setFormError(t("errors.generic", { email: CONTACT_EMAIL }));
      }
    } catch {
      setFormError(t("errors.network"));
    }
    setSubmitting(false);
  }

  if (sent) {
    return (
      <p data-testid="quote-success" role="status" className="note measure mt-6 font-medium">
        {t("success")}
      </p>
    );
  }

  return (
    <form noValidate onSubmit={onSubmit} className="relative mt-6 grid max-w-xl gap-5">
      <Field name="name" label={labels.name} error={errors.name} autoComplete="name" required />
      <Field name="email" label={labels.email} error={errors.email} type="email" autoComplete="email" required />
      <Field name="company" label={labels.company} error={errors.company} autoComplete="organization" required />
      <Field name="useCase" label={labels.useCase} error={errors.useCase} required defaultValue={initialUse} key={initialUse}>
        <option value="">{t("useCaseChoose")}</option>
        {USE_CASES.map((u) => (
          <option key={u} value={u}>
            {t(`useCaseOptions.${u}`)}
          </option>
        ))}
      </Field>
      <Field name="seats" label={labels.seats} error={errors.seats} />
      <Field name="aum" label={labels.aum} error={errors.aum} />
      <Field name="deployment" label={labels.deployment} error={errors.deployment} />
      <Field name="message" label={labels.message} error={errors.message} multiline required />
      <Honeypot label={t("honeypot")} />
      {formError ? (
        <div data-testid="form-error" role="alert" className="alert">
          {formError}
        </div>
      ) : null}
      <div>
        <button type="submit" data-testid="quote-submit" className="btn-action" disabled={submitting}>
          {submitting ? t("submitting") : t("submit")}
        </button>
      </div>
    </form>
  );
}
