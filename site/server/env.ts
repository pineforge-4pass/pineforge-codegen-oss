// Environment of the Pages Functions, the deployment mode and the live-payment
// guard. Shared by every Function; this directory is not routed.
import { goLiveBlockers } from "../lib/go-live.ts";
import locales from "../i18n/locales.json";

export interface Env {
  DB: D1Database;
  // Secrets (wrangler pages secret put ...).
  STRIPE_SECRET_KEY?: string;
  STRIPE_WEBHOOK_SECRET?: string;
  LICENSE_SIGNING_KEY?: string;
  RESEND_API_KEY?: string;
  TURNSTILE_SECRET?: string;
  // Vars (wrangler.jsonc).
  SITE_URL?: string;
  RESEND_FROM?: string;
  LICENSE_NOTIFY_TO?: string;
  STRIPE_TAX?: string;
  STRIPE_API_BASE?: string;
  RESEND_API_BASE?: string;
  /** JSON keyring that REPLACES the bundled one. Local development and tests only. */
  LICENSE_PUBLIC_KEYS?: string;
  /**
   * Test mode only: comma list of buyer addresses (exact, or "@domain") that
   * receive the license email. Other buyers get none (logged as skipped);
   * a live deployment ignores it.
   */
  TEST_EMAIL_ALLOWLIST?: string;
}

export type DeploymentMode = "test" | "live";

/** `live` iff the Stripe secret key is a live key (sk_live_ / rk_live_); anything else is test. */
export function deploymentMode(env: Env): DeploymentMode {
  const key = (env.STRIPE_SECRET_KEY ?? "").trim();
  return key.startsWith("sk_live_") || key.startsWith("rk_live_") ? "live" : "test";
}

/**
 * A development/test override (STRIPE_API_BASE, RESEND_API_BASE,
 * LICENSE_PUBLIC_KEYS). A deployment holding a live Stripe key ignores them,
 * so a stray override can neither send the live key elsewhere nor replace the
 * trusted keyring.
 */
export function testOverride(env: Env, name: "STRIPE_API_BASE" | "RESEND_API_BASE" | "LICENSE_PUBLIC_KEYS"): string {
  const value = (env[name] ?? "").trim();
  if (value && deploymentMode(env) === "live") {
    console.error(`[env] ${name} is ignored on a live deployment`);
    return "";
  }
  return value;
}

/** Why live payments must not be taken yet; [] once the owner has cleared every item. */
export function liveBlockReasons(): string[] {
  return goLiveBlockers();
}

/** Live key while the agreement, prices or seller are not ready: no checkout, no live issuance. */
export function liveBlocked(env: Env): boolean {
  return deploymentMode(env) === "live" && liveBlockReasons().length > 0;
}

/** Public origin of the site without a trailing slash (SITE_URL, else the request's origin). */
export function siteUrl(env: Env, request: Request): string {
  const configured = (env.SITE_URL ?? "").trim().replace(/\/+$/, "");
  return configured || new URL(request.url).origin;
}

const LOCALES: readonly string[] = (locales as { locales: string[] }).locales;
const DEFAULT_LOCALE: string = (locales as { defaultLocale: string }).defaultLocale;

/** A supported locale (it becomes part of redirect URLs), else the default one. */
export function safeLocale(x: unknown): string {
  return typeof x === "string" && LOCALES.includes(x) ? x : DEFAULT_LOCALE;
}
