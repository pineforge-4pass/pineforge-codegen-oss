// Is the Commercial License Agreement final? One answer for the build guard
// (scripts/guard-live.mjs) and the generated AGREEMENT_IS_DRAFT that the pages
// and Functions read (scripts/gen-build-info.mjs).
//
// It fails closed: the agreement is final only when ALL of these hold, so a
// marker that drifts (a hyphen for the em dash, a re-wrapped or reworded
// line) still reads as draft.
//   - the DRAFT marker is absent;
//   - a line exactly "Status: final" is present;
//   - a "Version:" line exists and its value does not contain "draft";
//   - no line contains "DRAFT" in capitals;
//   - no bracketed text remains other than Markdown links and references:
//     "[text](url)", "[text][ref]", "[ref]: url" and footnotes "[^1]" are
//     fine; any other "[...]", also one wrapped across lines ("[TBD]",
//     "[30 days]", "[REFUND POLICY]"), is a placeholder.

export const AGREEMENT_MARKER = "DRAFT — requires review by counsel before go-live";

export interface AgreementStatus {
  isDraft: boolean;
  /** Why it is a draft; empty when final. */
  reasons: string[];
  version: string | null;
}

export function agreementVersion(text: string): string | null {
  // "Version: x", "**Version:** x", "**Version**: x"
  const m = text.match(/^[\s>*_]*Version[\s*_]*:[\s*_]*`?([^\s*`]+)/im);
  return m ? m[1] : null;
}

export function agreementStatus(text: string): AgreementStatus {
  const reasons: string[] = [];
  if (text.includes(AGREEMENT_MARKER)) reasons.push(`the "${AGREEMENT_MARKER}" marker is present`);
  if (!/^Status: final$/m.test(text.replace(/\r\n?/g, "\n"))) reasons.push('no line "Status: final"');
  const version = agreementVersion(text);
  if (!version) reasons.push('no "Version:" line');
  else if (/draft/i.test(version)) reasons.push(`the version "${version}" is a draft version`);
  const draftLine = text.split(/\r\n?|\n/).find((line) => line.includes("DRAFT"));
  if (draftLine !== undefined) reasons.push(`a line still says DRAFT: "${draftLine.trim().slice(0, 80)}"`);
  const placeholders = bracketPlaceholders(text);
  if (placeholders.length) {
    reasons.push(`${placeholders.length} placeholder(s) remain, e.g. [${placeholders[0].replace(/\s+/g, " ")}]`);
  }
  return { isDraft: reasons.length > 0, reasons, version };
}

/** Bracketed text that is not Markdown link syntax (see the rules above). */
export function bracketPlaceholders(text: string): string[] {
  const found: string[] = [];
  for (const m of text.matchAll(/\[([^\[\]]*)\]/g)) {
    const start = m.index ?? 0;
    const end = start + m[0].length;
    const inner = m[1];
    const next = text.charAt(end);
    const prev = start > 0 ? text.charAt(start - 1) : "";
    const lineStart = start === 0 || text.charAt(start - 1) === "\n";
    if (next === "(" || next === "[") continue; // [text](url), [text][ref]
    if (prev === "]") continue; // the [ref] of [text][ref]
    if (inner.startsWith("^")) continue; // footnote [^1]
    if (lineStart && next === ":") continue; // reference definition [ref]: url
    found.push(inner);
  }
  return found;
}
