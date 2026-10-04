"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { day, scopeText } from "./license-format";
import { KEYS_PATH } from "./paths";

interface PublicLicense {
  id: string;
  product: string;
  licensee: { company: string; country: string };
  tier: string;
  option: string;
  scope: Record<string, unknown> | null;
  term: { months: number; validFrom: string; validUntil: string };
  issuedAt: string;
  mode: "test" | "live";
  agreement: { version: string; sha256: string };
  kid: string;
  revokedAt: string | null;
  revokeReason: string | null;
}

interface VerifyResult {
  valid: boolean;
  status: string;
  mode: "test" | "live" | null;
  license: PublicLicense | null;
}

type Shown = VerifyResult | { failed: true };

const STATUSES = new Set([
  "valid",
  "revoked",
  "expired",
  "not_yet_valid",
  "test_mode",
  "invalid_signature",
  "unknown_key",
  "not_found",
  "malformed",
]);

const MALFORMED: VerifyResult = { valid: false, status: "malformed", mode: null, license: null };

function isResult(x: unknown): x is VerifyResult {
  return typeof x === "object" && x !== null && typeof (x as VerifyResult).status === "string";
}

export function VerifyPanel() {
  const t = useTranslations("verify");
  const tc = useTranslations("common");
  const tcert = useTranslations("certificate");
  const [id, setId] = useState("");
  const [json, setJson] = useState("");
  const [idError, setIdError] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<Shown | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  async function checkId(raw: string) {
    const value = raw.trim();
    if (!value) {
      setIdError(t("idRequired"));
      return;
    }
    setIdError(null);
    setBusy(true);
    try {
      const res = await fetch(`/api/verify?id=${encodeURIComponent(value)}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const data: unknown = await res.json().catch(() => null);
      if (res.status === 400) {
        setIdError(t("idRequired"));
        setShown(null);
      } else if (isResult(data)) setShown(data);
      else setShown({ failed: true });
    } catch {
      setShown({ failed: true });
    }
    setBusy(false);
  }

  async function checkJson(text: string) {
    if (!text.trim()) {
      setJsonError(t("jsonRequired"));
      return;
    }
    setJsonError(null);
    try {
      JSON.parse(text);
    } catch {
      setShown(MALFORMED);
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: text,
      });
      const data: unknown = await res.json().catch(() => null);
      if (isResult(data)) setShown(data);
      else if (res.status === 400) setShown(MALFORMED);
      else setShown({ failed: true });
    } catch {
      setShown({ failed: true });
    }
    setBusy(false);
  }

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("id");
    if (q) {
      setId(q);
      void checkId(q);
    }
    // Run once, on load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setJson(text);
    await checkJson(text);
  }

  const onIdSubmit = (e: FormEvent) => {
    e.preventDefault();
    void checkId(id);
  };
  const onJsonSubmit = (e: FormEvent) => {
    e.preventDefault();
    void checkJson(json);
  };

  return (
    <div className="mt-10 grid gap-14">
      <section aria-labelledby="by-id" className="border-t border-hair pt-5">
        <h2 id="by-id" className="subheading">
          {t("byIdHeading")}
        </h2>
        <form noValidate onSubmit={onIdSubmit} className="mt-4 grid max-w-xl gap-4">
          <div>
            <label htmlFor="verify-id" className="field-label">
              {t("idLabel")}
            </label>
            <span id="verify-id-hint" className="field-hint">
              {t("idHint")}
            </span>
            <input
              id="verify-id"
              name="id"
              type="text"
              className="input mono"
              autoComplete="off"
              spellCheck={false}
              value={id}
              onChange={(e) => setId(e.target.value)}
              aria-invalid={idError ? true : undefined}
              aria-describedby={"verify-id-hint" + (idError ? " error-id" : "")}
            />
            {idError ? (
              <p id="error-id" data-testid="error-id" className="field-error">
                {idError}
              </p>
            ) : null}
          </div>
          <div>
            <button type="submit" data-testid="verify-id-submit" className="btn-action" disabled={busy}>
              {t("idSubmit")}
            </button>
          </div>
        </form>
      </section>

      <section aria-labelledby="by-file" className="border-t border-hair pt-5">
        <h2 id="by-file" className="subheading">
          {t("byFileHeading")}
        </h2>
        <form noValidate onSubmit={onJsonSubmit} className="mt-4 grid max-w-2xl gap-4">
          <div>
            <label htmlFor="verify-license" className="field-label">
              {t("jsonLabel")}
            </label>
            <textarea
              id="verify-license"
              name="license"
              className="input mono"
              rows={8}
              spellCheck={false}
              value={json}
              onChange={(e) => setJson(e.target.value)}
              aria-invalid={jsonError ? true : undefined}
              aria-describedby={jsonError ? "error-license" : undefined}
            />
            {jsonError ? (
              <p id="error-license" data-testid="error-license" className="field-error">
                {jsonError}
              </p>
            ) : null}
          </div>
          <div>
            <button type="submit" data-testid="verify-json-submit" className="btn" disabled={busy}>
              {t("jsonSubmit")}
            </button>
          </div>
          <div>
            <label htmlFor="verify-file" className="field-label">
              {t("fileLabel")}
            </label>
            <input
              id="verify-file"
              name="file"
              type="file"
              accept="application/json,.json"
              onChange={onFile}
              className="block min-h-11 py-2 text-[15px] file:mr-4 file:min-h-11 file:cursor-pointer file:border file:border-ink file:bg-transparent file:px-4 file:font-semibold file:text-ink"
            />
          </div>
        </form>
      </section>

      <div ref={resultRef} aria-live="polite">
        {busy ? <p>{t("checking")}</p> : null}
        {!busy && shown ? <Result shown={shown} t={t} tc={tc} tcert={tcert} /> : null}
      </div>

      <p className="small measure">
        {t.rich("offline", {
          link: (chunks) => (
            <a className="link mono" href={KEYS_PATH}>
              {chunks}
            </a>
          ),
        })}
      </p>
    </div>
  );
}

type T = ReturnType<typeof useTranslations>;

function Result({ shown, t, tc, tcert }: { shown: Shown; t: T; tc: T; tcert: T }) {
  if ("failed" in shown) {
    return (
      <div data-testid="verify-result" data-status="error" data-valid="false" className="alert measure">
        {t("status.error")}
      </div>
    );
  }
  const status = STATUSES.has(shown.status) ? shown.status : "malformed";
  const key = status === "valid" && (shown.mode === "test" || shown.license?.mode === "test") ? "validTest" : status;
  const lic = shown.license;
  const tierName = (id: string) => (tc.has(`tiers.${id}`) ? tc(`tiers.${id}`) : id);
  const reason = (r: string | null) =>
    r === "refund" ? tcert("reasonRefund") : r ? r : tcert("reasonOther");
  return (
    <section
      data-testid="verify-result"
      data-status={status}
      data-valid={shown.valid ? "true" : "false"}
      aria-labelledby="verify-result-heading"
      className={"measure pt-5 " + (shown.valid ? "border-t-2 border-ink" : "border-t-2 border-ink")}
    >
      <h2 id="verify-result-heading" className="subheading">
        {t("resultHeading")}
      </h2>
      <p className={"mt-2 " + (shown.valid ? "text-[18px] font-semibold" : "alert")}>{t(`status.${key}`)}</p>
      {lic ? (
        <dl className="mt-6 grid gap-x-10 gap-y-4 sm:grid-cols-2">
          <Item label={t("details.licenseId")} mono>
            {lic.id}
          </Item>
          <Item label={t("details.company")}>
            <span data-testid="verify-company" className="font-medium">
              {lic.licensee?.company}
            </span>
          </Item>
          <Item label={t("details.country")}>{lic.licensee?.country}</Item>
          <Item label={t("details.tier")}>{tierName(lic.tier)}</Item>
          <Item label={t("details.scope")}>{scopeText(lic.tier, lic.scope as never)}</Item>
          <Item label={t("details.term")} mono>
            {tc("termRange", { from: day(lic.term?.validFrom), until: day(lic.term?.validUntil) })}
          </Item>
          <Item label={t("details.issued")} mono>
            {day(lic.issuedAt)}
          </Item>
          <Item label={t("details.mode")}>{lic.mode === "test" ? t("details.modeTest") : t("details.modeLive")}</Item>
          <Item label={t("details.agreement")}>
            {t("details.agreementValue", { version: lic.agreement?.version ?? "" })}
          </Item>
          <Item label={t("details.kid")} mono>
            {lic.kid}
          </Item>
          <div className="sm:col-span-2">
            <dt className="small">{t("details.agreementHash")}</dt>
            <dd className="mono text-[13px] break-all">{lic.agreement?.sha256}</dd>
          </div>
          {lic.revokedAt ? (
            <div className="sm:col-span-2">
              <dt className="small">{t("details.revoked")}</dt>
              <dd>{t("details.revokedValue", { date: day(lic.revokedAt), reason: reason(lic.revokeReason) })}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}

function Item({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <dt className="small">{label}</dt>
      <dd className={mono ? "mono break-all" : undefined}>{children}</dd>
    </div>
  );
}
