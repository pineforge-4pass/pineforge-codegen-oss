// Is the Commercial License Agreement final? One answer for the build guard
// (scripts/guard-live.mjs) and the generated AGREEMENT_IS_DRAFT that the pages
// and Functions read (scripts/gen-build-info.mjs).
//
// The agreement is final only when its exact bytes were approved: the sha256
// of legal/commercial-license-agreement.md equals agreementApprovedSha256 in
// config/commerce.json (null: not approved). Any edit after approval makes it
// a draft again. On top of that, these text checks must all pass:
//   - the marker "DRAFT — requires review by counsel before go-live" is absent,
//     no line contains "DRAFT" in capitals, and no line matches
//     "draft <dash> requires review" in any case;
//   - the notice phrases "no one can accept it" and "this notice is removed"
//     are absent;
//   - a line exactly "Status: final" is present;
//   - a "Version:" line exists, and no "Version:" line, nor the next non-empty
//     line when a value is not on the same line, contains "draft";
//   - none of these phrases appears anywhere (any case): "owner to confirm",
//     "owner to provide", "owner to decide", "counsel to confirm", "set by
//     counsel", "template prepared for review", "not yet offered";
//   - no "[" is followed, before any "]", by "owner" or "counsel" (closed or
//     not);
//   - no bracket placeholder: any "[...]" other than a Markdown link
//     "[text](url)", reference "[text][ref]", definition "[ref]: url" or
//     footnote "[^1]", and bracket text in capitals (two letters or more;
//     digits, spaces, "-", "_" and dashes allowed; wrapped lines too) in any
//     of those forms.

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

export interface AgreementApproval {
  /** sha256 (hex) of the agreement file's exact bytes. */
  sha256: string;
  /** config/commerce.json agreementApprovedSha256; null when not approved. */
  approvedSha256: string | null;
}

const OPEN_PHRASES = [
  "owner to confirm",
  "owner to provide",
  "owner to decide",
  "counsel to confirm",
  "set by counsel",
  "template prepared for review",
  "not yet offered",
];

export function agreementStatus(text: string, approval: AgreementApproval): AgreementStatus {
  const reasons: string[] = [];
  if (!approval.approvedSha256) {
    reasons.push(`agreementApprovedSha256 is not set (the agreement's sha256 is ${approval.sha256})`);
  } else if (approval.approvedSha256 !== approval.sha256) {
    reasons.push(`the agreement's sha256 ${approval.sha256} is not the approved ${approval.approvedSha256}`);
  }
  const flatText = text.replace(/\s+/g, " ").toLowerCase();
  for (const phrase of OPEN_PHRASES) {
    if (flatText.includes(phrase)) reasons.push(`the text still says "${phrase}"`);
  }
  if (/\[[^\[\]]*\b(owner|counsel)\b/i.test(text)) reasons.push("a bracket naming the owner or counsel remains");
  if (text.includes(AGREEMENT_MARKER)) reasons.push(`the "${AGREEMENT_MARKER}" marker is present`);
  if (!/^Status: final$/m.test(text.replace(/\r\n?/g, "\n"))) reasons.push('no line "Status: final"');
  const version = agreementVersion(text);
  const lines = text.split(/\r\n?|\n/);
  if (!version) reasons.push('no "Version:" line');
  // agreementVersion() also reads a value split from its label ("Version\n: x").
  else if (/draft/i.test(version)) reasons.push(`the version "${version}" is a draft version`);
  // Every Version: line, and the next non-empty line when its value is not on the same line.
  lines.forEach((line, i) => {
    const m = line.match(/^[\s>*_]*Version[\s*_]*:(.*)$/i);
    if (!m) return;
    const next = /[A-Za-z0-9]/.test(m[1]) ? "" : (lines.slice(i + 1).find((l) => l.trim() !== "") ?? "");
    if (/draft/i.test(line) || /draft/i.test(next)) {
      reasons.push(`a version line is a draft version: "${line.trim()}${next ? " " + next.trim() : ""}"`);
    }
  });
  const draftLine = lines.find((line) => line.includes("DRAFT") || /draft\s*[—–-]+\s*requires review/i.test(line));
  if (draftLine !== undefined) reasons.push(`a line still says DRAFT: "${draftLine.trim().slice(0, 80)}"`);
  if (/no one can accept it|this notice is removed/i.test(text.replace(/\s+/g, " "))) {
    reasons.push("the draft notice paragraph is still present");
  }
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
    // Whatever form it takes, bracket text is a placeholder when it is in
    // capitals (letters, digits, spaces, "-", "_", dashes; two letters or more,
    // also wrapped across lines) or names the owner or counsel. All-caps link
    // text such as [LICENSE](url) is refused too: word such links differently.
    const flat = inner.replace(/\s+/g, " ").trim().replace(/^\^/, "");
    if ((/^[A-Z0-9 _\-—–]+$/.test(flat) && (flat.match(/[A-Z]/g) ?? []).length >= 2) || /owner|counsel/i.test(flat)) {
      found.push(inner);
      continue;
    }
    if (next === "(" || next === "[") continue; // [text](url), [text][ref]
    if (prev === "]") continue; // the [ref] of [text][ref]
    if (inner.startsWith("^")) continue; // footnote [^1]
    if (lineStart && next === ":") continue; // reference definition [ref]: url
    found.push(inner);
  }
  return found;
}
