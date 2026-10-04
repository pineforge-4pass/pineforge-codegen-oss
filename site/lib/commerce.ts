// Plans, prices and seller facts from config/commerce.json. Pure functions:
// the page, the checkout Function and the scripts pass the config in. The
// server prices every order from this config, never from the client.
import type { LicenseScope } from "./license.ts";

export type TierId = "team" | "fund" | "oem";

export interface TierOption {
  id: string;
  /** Annual price in minor units of `currency` (cents). */
  annual: number;
  seats?: number;
  aumBandUsd?: [number, number];
  deployment?: { products: number; endUsers: number };
}

export interface Tier {
  id: TierId;
  options: TierOption[];
  quoteAbove: string;
}

export interface CommerceConfig {
  pricesArePlaceholders: boolean;
  /** sha256 of the counsel-approved agreement file; null until approved (lib/agreement-status.ts). */
  agreementApprovedSha256: string | null;
  currency: string;
  seller: { displayName: string; legalName: string | null; contactEmail: string };
  termMonths: number;
  tiers: Tier[];
}

export const TIER_IDS: readonly TierId[] = ["team", "fund", "oem"];

const TIER_NAMES: Record<TierId, string> = { team: "Team", fund: "Fund", oem: "OEM / Embedded" };

/** English tier name, used in Stripe product names and emails. */
export function tierName(id: string): string {
  return TIER_NAMES[id as TierId] ?? id;
}

export function findPlan(
  c: CommerceConfig,
  tierId: unknown,
  optionId: unknown,
): { tier: Tier; option: TierOption } | null {
  if (typeof tierId !== "string" || typeof optionId !== "string") return null;
  const tier = c.tiers.find((t) => t.id === tierId);
  const option = tier?.options.find((o) => o.id === optionId);
  return tier && option ? { tier, option } : null;
}

export function scopeOf(option: TierOption): LicenseScope {
  const s: LicenseScope = {};
  if (option.seats !== undefined) s.seats = option.seats;
  if (option.aumBandUsd) s.aumBandUsd = [option.aumBandUsd[0], option.aumBandUsd[1]];
  if (option.deployment) {
    s.products = option.deployment.products;
    s.endUsers = option.deployment.endUsers;
  }
  return s;
}

/** $25M, $1.5B, $500K */
export function formatUsdShort(n: number): string {
  const fmt = (v: number, unit: string) => `$${Number.isInteger(v) ? v : v.toFixed(1)}${unit}`;
  if (n >= 1e9) return fmt(n / 1e9, "B");
  if (n >= 1e6) return fmt(n / 1e6, "M");
  if (n >= 1e3) return fmt(n / 1e3, "K");
  return `$${n}`;
}

const int = (n: number) => new Intl.NumberFormat("en-US").format(n);
const plural = (n: number, one: string, many: string) => `${int(n)} ${n === 1 ? one : many}`;

/** Human scope of an option, e.g. "5 seats", "AUM up to $25M, 10 seats", "1 product, up to 1,000 end users". */
export function scopeSummary(tier: Tier, option: TierOption): string {
  const parts: string[] = [];
  if (option.aumBandUsd) {
    const [lo, hi] = option.aumBandUsd;
    parts.push(lo <= 0 ? `AUM up to ${formatUsdShort(hi)}` : `AUM ${formatUsdShort(lo)}–${formatUsdShort(hi)}`);
  }
  if (option.deployment) {
    parts.push(plural(option.deployment.products, "product", "products"));
    parts.push(`up to ${plural(option.deployment.endUsers, "end user", "end users")}`);
  }
  if (option.seats !== undefined) parts.push(plural(option.seats, "seat", "seats"));
  return parts.join(", ") || tierName(tier.id);
}

/** "PineForge Codegen commercial license — Team, 5 seats, 12 months" */
export function productName(c: CommerceConfig, tier: Tier, option: TierOption): string {
  return `PineForge Codegen commercial license — ${tierName(tier.id)}, ${scopeSummary(tier, option)}, ${c.termMonths} months`;
}

/** Minor units to a currency string: 240000 usd -> "$2,400.00". Two-decimal currencies only (see validate). */
export function formatMoney(amountMinor: number, currency: string, locale = "en-US"): string {
  return new Intl.NumberFormat(locale, { style: "currency", currency: currency.toUpperCase() }).format(amountMinor / 100);
}

const TWO_DECIMAL_CURRENCIES = new Set(["usd", "eur", "gbp", "chf", "cad", "aud", "sgd", "nzd"]);
const posInt = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n > 0;

/** Schema check of config/commerce.json. Returns a list of problems; [] means valid. */
export function validateCommerceConfig(raw: unknown): string[] {
  const errs: string[] = [];
  const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
  if (!isObj(raw)) return ["config must be an object"];
  if (typeof raw.pricesArePlaceholders !== "boolean") errs.push("pricesArePlaceholders must be a boolean");
  if (!(raw.agreementApprovedSha256 === null || (typeof raw.agreementApprovedSha256 === "string" && /^[0-9a-f]{64}$/.test(raw.agreementApprovedSha256)))) {
    errs.push("agreementApprovedSha256 must be null or 64 lowercase hex characters");
  }
  if (typeof raw.currency !== "string" || !TWO_DECIMAL_CURRENCIES.has(raw.currency)) {
    errs.push(`currency must be one of ${[...TWO_DECIMAL_CURRENCIES].join(", ")} (lowercase)`);
  }
  if (!posInt(raw.termMonths)) errs.push("termMonths must be a positive integer");
  const seller = raw.seller;
  if (!isObj(seller)) errs.push("seller must be an object");
  else {
    if (typeof seller.displayName !== "string" || !seller.displayName.trim()) errs.push("seller.displayName is required");
    if (seller.legalName !== null && (typeof seller.legalName !== "string" || !seller.legalName.trim())) {
      errs.push("seller.legalName must be null or a non-empty string");
    }
    if (typeof seller.contactEmail !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(seller.contactEmail)) {
      errs.push("seller.contactEmail must be an email address");
    }
  }
  if (!Array.isArray(raw.tiers) || raw.tiers.length === 0) return [...errs, "tiers must be a non-empty array"];
  const seenTiers = new Set<string>();
  for (const [ti, t] of raw.tiers.entries()) {
    const at = `tiers[${ti}]`;
    if (!isObj(t)) {
      errs.push(`${at} must be an object`);
      continue;
    }
    if (!TIER_IDS.includes(t.id as TierId)) errs.push(`${at}.id must be one of ${TIER_IDS.join(", ")}`);
    if (seenTiers.has(String(t.id))) errs.push(`${at}.id duplicates ${String(t.id)}`);
    seenTiers.add(String(t.id));
    if (typeof t.quoteAbove !== "string" || !t.quoteAbove.trim()) errs.push(`${at}.quoteAbove is required`);
    if (!Array.isArray(t.options) || t.options.length === 0) {
      errs.push(`${at}.options must be a non-empty array`);
      continue;
    }
    const seenOpts = new Set<string>();
    for (const [oi, o] of t.options.entries()) {
      const ao = `${at}.options[${oi}]`;
      if (!isObj(o)) {
        errs.push(`${ao} must be an object`);
        continue;
      }
      if (typeof o.id !== "string" || !/^[a-z0-9][a-z0-9-]{0,31}$/.test(o.id)) errs.push(`${ao}.id must match [a-z0-9-]`);
      if (seenOpts.has(String(o.id))) errs.push(`${ao}.id duplicates ${String(o.id)}`);
      seenOpts.add(String(o.id));
      if (!posInt(o.annual)) errs.push(`${ao}.annual must be a positive integer (minor units)`);
      if (o.seats !== undefined && !posInt(o.seats)) errs.push(`${ao}.seats must be a positive integer`);
      if (t.id === "team" && o.seats === undefined) errs.push(`${ao}: team options need seats`);
      if (t.id === "fund") {
        const b = o.aumBandUsd;
        if (!Array.isArray(b) || b.length !== 2 || typeof b[0] !== "number" || typeof b[1] !== "number" || b[0] < 0 || b[1] <= b[0]) {
          errs.push(`${ao}.aumBandUsd must be [low, high] with 0 <= low < high`);
        }
        if (o.seats === undefined) errs.push(`${ao}: fund options need seats`);
      }
      if (t.id === "oem") {
        const d = o.deployment;
        if (!isObj(d) || !posInt(d.products) || !posInt(d.endUsers)) {
          errs.push(`${ao}.deployment must be { products, endUsers } positive integers`);
        }
      }
    }
  }
  return errs;
}
