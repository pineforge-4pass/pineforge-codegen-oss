"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { SignedLicense } from "@/lib/license";
import { CONTACT_EMAIL, localePath } from "./paths";
import { day, scopeText } from "./license-format";

interface OrderLicense {
  id: string;
  status: "active" | "revoked";
  mode: "test" | "live";
  tier: string;
  option: string;
  scope: Record<string, unknown> | null;
  validFrom: string;
  validUntil: string;
  issuedAt: string;
  revokedAt: string | null;
  revokeReason: string | null;
  certificateUrl: string;
  verifyUrl: string;
}

interface OrderResponse {
  status: string;
  order: {
    id: string;
    company: string;
    country: string;
    tier: string;
    option: string;
    productName: string;
    scopeSummary: string;
    amountSubtotal: number;
    currency: string;
    mode: string;
    createdAt: string;
    paidAt: string | null;
  } | null;
  license: OrderLicense | null;
  signedLicense: SignedLicense | null;
}

type State =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "not_found" }
  | { kind: "error" }
  | { kind: "data"; data: OrderResponse; timedOut: boolean };

const POLL_MS = 1500;
const POLL_LIMIT_MS = 120_000;
const KNOWN = new Set(["pending", "issued", "refunded", "revoked", "expired", "failed", "mismatch"]);

export function OrderStatus() {
  const t = useTranslations("order");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    const sessionId = new URLSearchParams(window.location.search).get("session_id");
    if (!sessionId) {
      setState({ kind: "missing" });
      return;
    }
    let stopped = false;
    let timer: number | undefined;
    const started = Date.now();
    const again = () => {
      timer = window.setTimeout(poll, POLL_MS);
    };
    async function poll() {
      try {
        const res = await fetch(`/api/order?session_id=${encodeURIComponent(sessionId as string)}`, {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (stopped) return;
        if (res.status === 404) return setState({ kind: "not_found" });
        if (res.status === 400) return setState({ kind: "missing" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as OrderResponse;
        if (stopped) return;
        const pending = data.status === "pending";
        const timedOut = pending && Date.now() - started > POLL_LIMIT_MS;
        setState({ kind: "data", data, timedOut });
        if (pending && !timedOut) again();
      } catch {
        if (stopped) return;
        if (Date.now() - started > POLL_LIMIT_MS) setState({ kind: "error" });
        else again();
      }
    }
    void poll();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  const signedJson =
    state.kind === "data" && state.data.signedLicense ? JSON.stringify(state.data.signedLicense, null, 2) + "\n" : null;

  useEffect(() => {
    if (!signedJson) return;
    const url = URL.createObjectURL(new Blob([signedJson], { type: "application/json" }));
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [signedJson]);

  if (state.kind === "loading") {
    return (
      <div data-testid="order-status" data-status="loading" className="mt-8" aria-live="polite">
        <p>{tc("loading")}</p>
      </div>
    );
  }
  if (state.kind !== "data") {
    return (
      <div data-testid="order-status" data-status={state.kind} className="mt-8" aria-live="polite">
        <p className="alert measure">{t(`status.${state.kind}`, { email: CONTACT_EMAIL })}</p>
      </div>
    );
  }

  const { data, timedOut } = state;
  const status = KNOWN.has(data.status) ? data.status : "pending";
  const lic = data.license;
  const order = data.order;
  const text =
    status === "pending" && timedOut ? t("status.pendingTimeout", { email: CONTACT_EMAIL }) : t(`status.${status}`, { email: CONTACT_EMAIL });
  const tierName = (id: string) => (tc.has(`tiers.${id}`) ? tc(`tiers.${id}`) : id);

  return (
    <div data-testid="order-status" data-status={status} className="mt-8">
      <div aria-live="polite">
        <p className={status === "issued" ? "lead measure font-medium" : "alert measure"}>{text}</p>
      </div>

      {lic ? (
        <section aria-labelledby="license-heading" className="mt-10 border-t border-ink pt-6">
          <h2 id="license-heading" className="subheading">
            {t("details.licenseId")}
          </h2>
          <p data-testid="license-id" className="mono mt-2 text-[clamp(18px,4.4vw,26px)] font-medium break-all">
            {lic.id}
          </p>
          {lic.mode === "test" ? <p className="note measure mt-4">{tc("testLicense")}</p> : null}
          <dl className="mt-6 grid gap-x-10 gap-y-4 sm:grid-cols-2">
            {order ? (
              <>
                <div>
                  <dt className="small">{t("details.company")}</dt>
                  <dd className="font-medium">{order.company}</dd>
                </div>
                <div>
                  <dt className="small">{t("details.country")}</dt>
                  <dd>{order.country}</dd>
                </div>
              </>
            ) : null}
            <div>
              <dt className="small">{t("details.tier")}</dt>
              <dd>{tierName(lic.tier)}</dd>
            </div>
            <div>
              <dt className="small">{t("details.scope")}</dt>
              <dd>{order?.scopeSummary || scopeText(lic.tier, lic.scope as never)}</dd>
            </div>
            <div>
              <dt className="small">{t("details.validity")}</dt>
              <dd className="mono">{tc("termRange", { from: day(lic.validFrom), until: day(lic.validUntil) })}</dd>
            </div>
          </dl>
          <ul className="mt-8 flex flex-wrap gap-x-8 gap-y-1">
            <li>
              <a
                data-testid="certificate-link"
                className="link inline-flex min-h-11 items-center font-medium"
                href={lic.certificateUrl || `/certificate/${lic.id}`}
              >
                {t("certificate")}
              </a>
            </li>
            {blobUrl ? (
              <li>
                <a
                  data-testid="license-download"
                  className="link inline-flex min-h-11 items-center font-medium"
                  href={blobUrl}
                  download={`${lic.id}.json`}
                >
                  {t("download")}
                </a>
              </li>
            ) : null}
            <li>
              <a
                data-testid="verify-link"
                className="link inline-flex min-h-11 items-center font-medium"
                href={localePath(locale, `verify/?id=${encodeURIComponent(lic.id)}`)}
              >
                {t("verify")}
              </a>
            </li>
          </ul>
          <p className="small measure mt-4">{t("keepFile")}</p>
        </section>
      ) : null}

      {status !== "expired" && status !== "failed" ? (
        <p className="small measure mt-8">{t("invoiceNote")}</p>
      ) : null}
    </div>
  );
}
