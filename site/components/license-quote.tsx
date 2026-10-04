import { LICENSE_QUOTES, LICENSE_QUOTE_SECTION, LICENSE_URL, type QuoteId } from "@/content/license-quotes";

/**
 * A verbatim LICENSE quotation. The interactive guide's copies carry the test
 * hooks; the static fallback's copies do not, so a hidden copy never matches.
 */
export function LicenseQuote({ id, hooks = true }: { id: QuoteId; hooks?: boolean }) {
  return (
    <figure className="m-0">
      <blockquote
        className="quote"
        cite={LICENSE_URL}
        data-testid={hooks ? "license-quote" : undefined}
        data-quote={hooks ? id : undefined}
      >
        {LICENSE_QUOTES[id]}
      </blockquote>
      <figcaption className="small mt-1 pl-[19px]">LICENSE — {LICENSE_QUOTE_SECTION[id]}</figcaption>
    </figure>
  );
}
