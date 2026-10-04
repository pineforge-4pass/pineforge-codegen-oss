import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { AGREEMENT_MARKER, agreementStatus as rawStatus } from "../lib/agreement-status.ts";

const sha = (text: string) => createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
// The text checks with this exact text approved, unless another approval is given.
const agreementStatus = (text: string, approvedSha256: string | null = sha(text)) =>
  rawStatus(text, { sha256: sha(text), approvedSha256 });

const body = (head: string) => `${head}

# PineForge Codegen — Commercial License Agreement

Version: 2027-01-15

**1. Parties.** PineForge Ltd, 1 Example Street, and the Licensee named on the Order.
See the [license text](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/LICENSE).
`;
const FINAL = body("Status: final");

test("a fully final agreement is final", () => {
  const s = agreementStatus(FINAL);
  assert.deepEqual(s.reasons, []);
  assert.equal(s.isDraft, false);
  assert.equal(s.version, "2027-01-15");
});

test("the exact DRAFT marker is a draft", () => {
  assert.equal(agreementStatus(`> **${AGREEMENT_MARKER}**\n` + FINAL).isDraft, true);
});

test("a drifted marker (hyphen for the em dash) without Status: final is a draft", () => {
  const s = agreementStatus(body("> **DRAFT - requires review by counsel before go-live**"));
  assert.equal(s.isDraft, true);
  assert.ok(s.reasons.some((r) => r.includes("Status: final")));
});

test("Markdown links, references and footnotes are not placeholders", () => {
  const md = FINAL + "\nSee [the terms](https://example.com/t), [the LICENSE][lic] and a note[^1].\n\n[lic]: https://example.com/l\n[^1]: A footnote.\n";
  assert.deepEqual(agreementStatus(md).reasons, []);
});

test("any other bracketed text is a placeholder, also wrapped across lines", () => {
  for (const ph of ["[TBD]", "[REFUND POLICY]", "[30 days]", "[LICENSOR\nLEGAL ENTITY]", "[x]"]) {
    const s = agreementStatus(FINAL + `\nText ${ph} here.\n`);
    assert.equal(s.isDraft, true, ph);
    assert.ok(s.reasons.some((r) => r.includes("placeholder")), ph);
  }
});

test("a line saying DRAFT in capitals is a draft", () => {
  const s = agreementStatus(FINAL + "\nThis DRAFT is for discussion.\n");
  assert.equal(s.isDraft, true);
  assert.ok(s.reasons.some((r) => r.includes("DRAFT")));
  assert.equal(agreementStatus(FINAL + "\nA draft in lowercase prose is fine.\n").isDraft, false);
});

test("marker removed but placeholders left is a draft", () => {
  for (const ph of ["[LICENSOR LEGAL ENTITY — owner to confirm]", "[GOVERNING LAW — to be set by Counsel]", "within [30] days"]) {
    const s = agreementStatus(FINAL + `\nText ${ph}.\n`);
    assert.equal(s.isDraft, true, ph);
    assert.ok(s.reasons.some((r) => r.includes("placeholder")), ph);
  }
});

test("marker removed and Status: final, but the version is still a draft version", () => {
  const s = agreementStatus(FINAL.replace("Version: 2027-01-15", "Version: draft-2026-10-04"));
  assert.equal(s.isDraft, true);
  assert.ok(s.reasons.some((r) => r.includes("draft version")));
  assert.equal(agreementStatus(FINAL.replace("Version: 2027-01-15\n", "")).isDraft, true, "no version line");
  assert.equal(agreementStatus(FINAL.replace("Status: final", "Status: Final ")).isDraft, true, "exact line only");
});

test("the agreement in this repository is a draft today", () => {
  const text = readFileSync(new URL("../legal/commercial-license-agreement.md", import.meta.url), "utf8");
  assert.equal(agreementStatus(text, null).isDraft, true, "not approved");
  assert.equal(agreementStatus(text).isDraft, true, "even if its hash were approved, the text checks fail");
});

test("residual draft forms: marker in any case, the notice paragraph, a draft version line", () => {
  for (const extra of [
    "> **Draft — requires review by counsel before go-live**",
    "draft - requires review",
    "It is not yet offered: no one can accept it until counsel has reviewed it.",
    "This holds until this notice is removed.",
  ]) {
    assert.equal(agreementStatus(FINAL + `\n${extra}\n`).isDraft, true, extra);
  }
  assert.equal(agreementStatus(FINAL.replace("Version: 2027-01-15", "Version: 2027-01-15 (draft)")).isDraft, true);
});

test("ALL-CAPS bracket text is a placeholder in link, reference and footnote forms", () => {
  for (const extra of ["[CITY][COUNTRY]", "[NAME](https://example.com)", "[NAME]: https://example.com", "text[^LEGAL ENTITY]"]) {
    const s = agreementStatus(FINAL + `\n${extra}\n`);
    assert.equal(s.isDraft, true, extra);
    assert.ok(s.reasons.some((r) => r.includes("placeholder")), extra);
  }
});

test("a draft version on the line after Version: is a draft", () => {
  const text = FINAL.replace("Version: 2027-01-15", "Version:\ndraft-2027-01-15");
  assert.equal(agreementStatus(text).isDraft, true);
});

test("placeholders in link forms: hyphens, underscores, digits, the agreement's own style, wrapped caps", () => {
  for (const extra of [
    "[WIND-DOWN PERIOD](x)",
    "[MID-TERM AUM][x]",
    "[30 DAYS](x)",
    "[LICENSOR_NAME](x)",
    '[LICENSOR ADDRESS — owner to confirm]("Licensor")',
    "[LICENSOR\nLEGAL ENTITY](x)",
    "[LICENSE](https://example.com/l)",
  ]) {
    const s = agreementStatus(FINAL + `\n${extra}\n`);
    assert.equal(s.isDraft, true, extra);
    assert.ok(s.reasons.some((r) => r.includes("placeholder")), extra);
  }
});

test("approval hash: null is draft; the approved bytes are final; any edit after approval is draft", () => {
  assert.equal(agreementStatus(FINAL, null).isDraft, true);
  assert.ok(agreementStatus(FINAL, null).reasons.some((r) => r.includes("agreementApprovedSha256")));
  assert.equal(agreementStatus(FINAL, sha(FINAL)).isDraft, false);
  assert.equal(agreementStatus(FINAL + " ", sha(FINAL)).isDraft, true, "one byte added after approval");
  const drafty = FINAL + "\nThis DRAFT stays.\n";
  assert.equal(agreementStatus(drafty, sha(drafty)).isDraft, true, "approved hash, draft signal left");
});

test("backstops: every Version line, open phrases anywhere, unclosed owner/counsel brackets", () => {
  const twoVersions = FINAL + "\nVersion: draft-2027-02\n";
  assert.equal(agreementStatus(twoVersions).isDraft, true, "a second Version line");
  const nextLine = FINAL.replace("Version: 2027-01-15", "Version:\n\n  release 2027-01-15 (draft)");
  assert.equal(agreementStatus(nextLine).isDraft, true, "value on the next non-empty line");
  for (const phrase of [
    "the owner to confirm",
    "Owner To Provide",
    "owner to\ndecide",
    "counsel to confirm",
    "as set by counsel",
    "A template prepared for review.",
    "It is not yet offered to anyone.",
    "[LICENSOR ENTITY — owner to confirm",
    "[the cap, per counsel",
  ]) {
    assert.equal(agreementStatus(FINAL + `\n${phrase}\n`).isDraft, true, phrase);
  }
});

test("a draft version split from its label is a draft", () => {
  for (const v of ["Version\n: draft-1", "**Version**\n: draft-1"]) {
    assert.equal(agreementStatus(FINAL.replace("Version: 2027-01-15", v)).isDraft, true, v);
  }
});
