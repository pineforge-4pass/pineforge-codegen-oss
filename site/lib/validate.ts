// Field validation shared by the client forms (inline errors) and the
// Functions (authoritative). Returns every error, not just the first.

export const HONEYPOT_FIELD = "website";

export type FieldError = { field: string; code: "required" | "too_long" | "invalid" };

export interface CheckoutInput {
  tier: string;
  option: string;
  company: string;
  country: string;
  buyerName: string;
  buyerEmail: string;
  reference: string;
  acceptAgreement: boolean;
  locale: string;
}

export type UseCase = "team" | "fund" | "oem" | "other";
export const USE_CASES: readonly UseCase[] = ["team", "fund", "oem", "other"];

export interface QuoteInput {
  name: string;
  email: string;
  company: string;
  useCase: UseCase;
  seats: string;
  aum: string;
  deployment: string;
  message: string;
  locale: string;
}

type Result<T> = { ok: true; value: T } | { ok: false; errors: FieldError[] };

const CONTROL = /[\u0000-\u001f\u007f]/;
const CONTROL_EXCEPT_NEWLINES = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

export function isEmail(s: string): boolean {
  return s.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s) && !s.includes("..");
}

function reader(b: Record<string, unknown>, errors: FieldError[]) {
  return (field: string, opts: { max: number; min?: number; required?: boolean; multiline?: boolean }): string => {
    const raw = b[field];
    const v = typeof raw === "string" ? raw.trim() : "";
    if (!v) {
      if (opts.required) errors.push({ field, code: "required" });
      return "";
    }
    if (v.length > opts.max) errors.push({ field, code: "too_long" });
    else if (v.length < (opts.min ?? 1) || (opts.multiline ? CONTROL_EXCEPT_NEWLINES : CONTROL).test(v)) {
      errors.push({ field, code: "invalid" });
    }
    return v;
  };
}

function localeOf(x: unknown): string {
  return typeof x === "string" && /^[a-z]{2}(-[A-Z]{2})?$/.test(x) ? x : "en";
}

export function validateCheckout(body: unknown): Result<CheckoutInput> {
  if (typeof body !== "object" || body === null) return { ok: false, errors: [{ field: "form", code: "invalid" }] };
  const b = body as Record<string, unknown>;
  const errors: FieldError[] = [];
  const text = reader(b, errors);
  const value: CheckoutInput = {
    tier: text("tier", { max: 32, required: true }),
    option: text("option", { max: 32, required: true }),
    company: text("company", { max: 200, min: 2, required: true }),
    country: text("country", { max: 80, min: 2, required: true }),
    buyerName: text("buyerName", { max: 200, required: true }),
    buyerEmail: text("buyerEmail", { max: 254, required: true }),
    reference: text("reference", { max: 100 }),
    acceptAgreement: b.acceptAgreement === true,
    locale: localeOf(b.locale),
  };
  if (value.buyerEmail && !isEmail(value.buyerEmail) && !errors.some((e) => e.field === "buyerEmail")) {
    errors.push({ field: "buyerEmail", code: "invalid" });
  }
  if (!value.acceptAgreement) errors.push({ field: "acceptAgreement", code: "required" });
  return errors.length ? { ok: false, errors } : { ok: true, value };
}

export function validateQuote(body: unknown): Result<QuoteInput> {
  if (typeof body !== "object" || body === null) return { ok: false, errors: [{ field: "form", code: "invalid" }] };
  const b = body as Record<string, unknown>;
  const errors: FieldError[] = [];
  const text = reader(b, errors);
  const useCase = text("useCase", { max: 16, required: true });
  const value: QuoteInput = {
    name: text("name", { max: 200, required: true }),
    email: text("email", { max: 254, required: true }),
    company: text("company", { max: 200, required: true }),
    useCase: useCase as UseCase,
    seats: text("seats", { max: 50 }),
    aum: text("aum", { max: 80 }),
    deployment: text("deployment", { max: 200 }),
    message: text("message", { max: 5000, required: true, multiline: true }),
    locale: localeOf(b.locale),
  };
  if (useCase && !USE_CASES.includes(useCase as UseCase) && !errors.some((e) => e.field === "useCase")) {
    errors.push({ field: "useCase", code: "invalid" });
  }
  if (value.email && !isEmail(value.email) && !errors.some((e) => e.field === "email")) {
    errors.push({ field: "email", code: "invalid" });
  }
  return errors.length ? { ok: false, errors } : { ok: true, value };
}
