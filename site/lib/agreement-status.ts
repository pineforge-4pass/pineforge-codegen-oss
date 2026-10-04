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
//   - no placeholder remains: no [...] mentioning "owner" or "counsel", and no
//     bracketed number such as [30].

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
  const placeholders = [...text.matchAll(/\[([^\]\n]*)\]/g)]
    .map((m) => m[1])
    .filter((inner) => /owner|counsel/i.test(inner) || /^\s*\d+(\.\d+)?\s*$/.test(inner));
  if (placeholders.length) {
    reasons.push(`${placeholders.length} placeholder(s) remain, e.g. [${placeholders[0]}]`);
  }
  return { isDraft: reasons.length > 0, reasons, version };
}
