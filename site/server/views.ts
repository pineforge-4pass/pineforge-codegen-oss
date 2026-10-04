// Public views of licenses: what the order, verify and certificate pages may
// show. Never the buyer's name or email.
import { scopeSummary, type Tier, type TierOption } from "../lib/commerce.ts";
import type { LicensePayload, LicenseScope } from "../lib/license.ts";
import type { LicenseRow } from "./db.ts";

export interface PublicLicense {
  id: string;
  product: string;
  licensee: { company: string; country: string };
  tier: string;
  option: string;
  scope: LicenseScope;
  term: { months: number; validFrom: string; validUntil: string };
  issuedAt: string;
  mode: "test" | "live";
  agreement: { version: string; sha256: string };
  kid: string;
  revokedAt: string | null;
  revokeReason: string | null;
}

export function publicLicense(payload: LicensePayload, kid: string, row: LicenseRow | null): PublicLicense {
  return {
    id: payload.id,
    product: payload.product,
    licensee: { company: payload.licensee.company, country: payload.licensee.country },
    tier: payload.tier,
    option: payload.option,
    scope: payload.scope,
    term: { months: payload.term.months, validFrom: payload.term.validFrom, validUntil: payload.term.validUntil },
    issuedAt: payload.issuedAt,
    mode: payload.mode,
    agreement: { version: payload.agreement.version, sha256: payload.agreement.sha256 },
    kid,
    revokedAt: row?.revoked_at ?? null,
    revokeReason: publicRevokeReason(row?.revoke_reason ?? null),
  };
}

/** Where `now` falls in the license term. */
export function termState(validFrom: string, validUntil: string, now = Date.now()): "not_yet_valid" | "expired" | "current" {
  const from = Date.parse(validFrom);
  const until = Date.parse(validUntil);
  if (Number.isNaN(from) || Number.isNaN(until)) return "expired";
  if (now < from) return "not_yet_valid";
  if (now >= until) return "expired";
  return "current";
}

/** The scope a signed license carries, in the plans page's words. */
export function scopeText(tierId: string, scope: LicenseScope): string {
  const option: TierOption = { id: "", annual: 0 };
  if (scope.seats !== undefined) option.seats = scope.seats;
  if (scope.aumBandUsd) option.aumBandUsd = scope.aumBandUsd;
  if (scope.products !== undefined && scope.endUsers !== undefined) {
    option.deployment = { products: scope.products, endUsers: scope.endUsers };
  }
  const tier: Tier = { id: tierId as Tier["id"], options: [option], quoteAbove: "" };
  return scopeSummary(tier, option);
}

/**
 * The revoke reason a public answer may carry: "refund", or "revoked" for any
 * other stored reason (dispute_lost included), so a dispute is never disclosed.
 */
export function publicRevokeReason(code: string | null): "refund" | "revoked" | null {
  if (!code) return null;
  return code === "refund" ? "refund" : "revoked";
}
