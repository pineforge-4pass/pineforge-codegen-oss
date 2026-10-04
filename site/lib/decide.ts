// "Do I need a commercial license?" — the decision guide's logic, kept pure
// so the page (static and interactive) and the unit tests share it.
//
// Order (LICENSE, "Additional Permission — Personal Trading" and "Commercial
// Use"): the four Commercial Uses first; any yes needs a commercial license.
// Then the Personal Trading test; then the base license's permitted purposes.
//
// One case the guide does not decide: an organization of the kinds the base
// license's "Noncommercial Organizations" clause lists. That clause makes its
// use a permitted purpose, while the supplemental sections call uses (1)-(4)
// Commercial Use and control in a conflict. Until counsel settles it, such an
// organization is asked to write first, whatever other uses it names.
import type { QuoteId } from "../content/license-quotes.ts";

export type QuestionId =
  | "othersCapital" // (1)
  | "organization" // (2)
  | "noncommercialOrg" // asked only after (2) is yes
  | "embedding" // (3)
  | "hosted" // (4)
  | "naturalPerson"
  | "ownAccount"
  | "ownCapital"
  | "noncommercial";

export type Answers = Partial<Record<QuestionId, boolean>>;

export type OutcomeId =
  | "commercial-team"
  | "commercial-fund"
  | "commercial-oem"
  | "commercial-quote"
  | "ask-first"
  | "personal-trading"
  | "permitted-noncommercial"
  | "commercial-other";

export type Use = 1 | 2 | 3 | 4;

export interface Outcome {
  id: OutcomeId;
  tier: "team" | "fund" | "oem" | null;
  uses: Use[];
  quotes: QuoteId[];
}

export const USE_QUESTIONS: readonly QuestionId[] = ["othersCapital", "organization", "embedding", "hosted"];
export const PERSONAL_TRADING_QUESTIONS: readonly QuestionId[] = ["naturalPerson", "ownAccount", "ownCapital"];
export const QUESTION_ORDER: readonly QuestionId[] = [
  "othersCapital",
  "organization",
  "noncommercialOrg",
  "embedding",
  "hosted",
  ...PERSONAL_TRADING_QUESTIONS,
  "noncommercial",
];

/** LICENSE use number for each of the first four questions. */
export const USE_OF: Partial<Record<QuestionId, Use>> = { othersCapital: 1, organization: 2, embedding: 3, hosted: 4 };

/** Which Commercial Uses each tier covers; smallest scope first. */
export const TIER_COVERS: ReadonlyArray<{ tier: "team" | "fund" | "oem"; uses: readonly Use[] }> = [
  { tier: "team", uses: [2] },
  { tier: "fund", uses: [1, 2] },
  { tier: "oem", uses: [2, 3, 4] },
];

const USE_QUOTE: Record<Use, QuoteId> = { 1: "use1", 2: "use2", 3: "use3", 4: "use4" };

/** The questions asked so far, in order, ending with the next unanswered one (if any). */
export function path(answers: Answers): QuestionId[] {
  const asked: QuestionId[] = [];
  for (const q of USE_QUESTIONS) {
    asked.push(q);
    if (answers[q] === undefined) return asked;
    if (q === "organization" && answers.organization === true) {
      asked.push("noncommercialOrg");
      if (answers.noncommercialOrg === undefined) return asked;
    }
  }
  if (USE_QUESTIONS.some((q) => answers[q] === true)) return asked;
  for (const q of PERSONAL_TRADING_QUESTIONS) {
    asked.push(q);
    if (answers[q] === undefined) return asked;
    if (answers[q] === false) break;
  }
  if (PERSONAL_TRADING_QUESTIONS.every((q) => answers[q] === true)) return asked;
  asked.push("noncommercial");
  return asked;
}

export function nextQuestion(answers: Answers): QuestionId | null {
  const p = path(answers);
  const last = p[p.length - 1];
  return answers[last] === undefined ? last : null;
}

export function decide(answers: Answers): Outcome | null {
  if (nextQuestion(answers) !== null) return null;
  const uses = USE_QUESTIONS.filter((q) => answers[q] === true).map((q) => USE_OF[q] as Use);
  if (answers.organization === true && answers.noncommercialOrg === true) {
    // Never resolved either way, whatever else applies: the other uses the
    // answers name are quoted beside the clause and the conflict sentence.
    return {
      id: "ask-first",
      tier: null,
      uses,
      quotes: ["noncommercialOrgs", ...uses.map((u) => USE_QUOTE[u]), "usesAreCommercial", "supplementalControl"],
    };
  }
  if (uses.length > 0) {
    const fit = TIER_COVERS.find((t) => uses.every((u) => t.uses.includes(u)));
    const quotes: QuoteId[] = [...uses.map((u) => USE_QUOTE[u]), "usesAreCommercial", "commercialUse"];
    return fit
      ? { id: `commercial-${fit.tier}`, tier: fit.tier, uses, quotes }
      : { id: "commercial-quote", tier: null, uses, quotes };
  }
  if (PERSONAL_TRADING_QUESTIONS.every((q) => answers[q] === true)) {
    return {
      id: "personal-trading",
      tier: null,
      uses: [],
      quotes: ["personalTradingGrant", "personalTradingDef", "personalTradingA", "personalTradingB"],
    };
  }
  if (answers.noncommercial === true) {
    return {
      id: "permitted-noncommercial",
      tier: null,
      uses: [],
      quotes: ["noncommercialPurposes", "personalUses"],
    };
  }
  return { id: "commercial-other", tier: null, uses: [], quotes: ["commercialUse"] };
}
