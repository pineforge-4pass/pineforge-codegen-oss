import type { Outcome } from "@/lib/decide";
import { localePath } from "./paths";

/** Where a result sends the reader: the recommended tier, the quote form, or nowhere. */
export function outcomeHref(locale: string, o: Outcome): string | null {
  if (o.tier) return localePath(locale, `plans/#tier-${o.tier}`);
  if (o.id === "commercial-quote" || o.id === "commercial-other") return localePath(locale, "contact/#quote");
  return null;
}
