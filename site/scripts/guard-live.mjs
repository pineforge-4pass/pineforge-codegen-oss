// Live-payment guard, run before every build. While the agreement is a draft,
// prices are placeholders or no selling entity is named, a build whose
// environment (or site/.dev.vars) holds a live Stripe key fails here.
import { agreementInfo, readCommerce, readDevVars } from "./site-files.mjs";

const LIVE = /^(sk|rk|pk)_live_/;
const NAMES = ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"];

const devVars = readDevVars();
const live = [];
for (const name of NAMES) {
  if (LIVE.test((process.env[name] ?? "").trim())) live.push(`${name} (environment)`);
  if (LIVE.test((devVars[name] ?? "").trim())) live.push(`${name} (.dev.vars)`);
}

const commerce = readCommerce();
const blockers = [];
const agreement = await agreementInfo();
for (const reason of agreement.draftReasons) {
  blockers.push(`legal/commercial-license-agreement.md is not final: ${reason}`);
}
if (commerce.pricesArePlaceholders) blockers.push("config/commerce.json: pricesArePlaceholders is true");
if (!commerce.seller?.legalName) blockers.push("config/commerce.json: seller.legalName is not set");

if (live.length && blockers.length) {
  console.error(`Live-payment guard: refusing to build with live Stripe keys: ${live.join(", ")}.`);
  console.error("Live payments stay disabled until every go-live condition holds:");
  for (const b of blockers) console.error(`  - ${b}`);
  console.error("Use test-mode keys (sk_test_ / pk_test_), or finish the go-live checklist in README.md.");
  process.exit(1);
}
console.log(
  live.length
    ? "live-payment guard: live keys present and every go-live condition holds"
    : `live-payment guard: no live keys${blockers.length ? ` (go-live blocked: ${blockers.length} condition(s) open)` : ""}`,
);
