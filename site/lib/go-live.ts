// What still blocks taking live payments. The pages show the preview notice
// while any blocker holds; the Functions refuse live checkout and live
// issuance (server/env.ts); the build guard refuses live keys
// (scripts/guard-live.mjs reads the same three conditions).
import { AGREEMENT_IS_DRAFT } from "./generated/build-info.ts";
import { commerce } from "./commerce-config.ts";

export function goLiveBlockers(): string[] {
  const reasons: string[] = [];
  if (AGREEMENT_IS_DRAFT) reasons.push("the Commercial License Agreement is not final");
  if (commerce.pricesArePlaceholders) reasons.push("prices are placeholders (pricesArePlaceholders is true)");
  if (!commerce.seller.legalName) reasons.push("the selling legal entity is not named (seller.legalName is null)");
  return reasons;
}

export function isPreview(): boolean {
  return goLiveBlockers().length > 0;
}
