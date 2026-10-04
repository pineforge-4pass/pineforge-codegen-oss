import { scopeSummary, type Tier, type TierId } from "@/lib/commerce";
import type { LicenseScope } from "@/lib/license";

/** A license scope in the words the plans page uses ("5 seats", "AUM up to $25M, 10 seats"). */
export function scopeText(tierId: string, scope: LicenseScope | null | undefined): string {
  const s = scope ?? {};
  const tier = { id: tierId as TierId, options: [], quoteAbove: "" } satisfies Tier;
  return scopeSummary(tier, {
    id: "",
    annual: 0,
    seats: s.seats,
    aumBandUsd: s.aumBandUsd,
    deployment:
      s.products !== undefined || s.endUsers !== undefined
        ? { products: s.products ?? 0, endUsers: s.endUsers ?? 0 }
        : undefined,
  });
}

/** "2026-10-04" from an ISO timestamp; "" for anything else. */
export function day(iso: unknown): string {
  return typeof iso === "string" ? iso.slice(0, 10) : "";
}
