// Offline check of a signed license file.
//
//   node scripts/verify-license.mjs <license.json> [--keyring <keyring.json>] [--strict]
//
// Checks the Ed25519 signature against the shipped keyring
// (keys/license-public-keys.json), or --keyring <file>, or the
// LICENSE_PUBLIC_KEYS environment variable (JSON). Revocation needs the online
// check (the site's verify page). Exit 0: signature valid; 1: not valid; 2: usage;
// 3 (only with --strict): the signature is valid but the license is a test
// license or outside its term.
import { readFileSync } from "node:fs";
import { KEYRING_FILE } from "./site-files.mjs";
import { parseKeyring, verifyLicenseSignature } from "../lib/license.ts";

const args = process.argv.slice(2);
const ki = args.indexOf("--keyring");
const keyringPath = ki >= 0 ? args[ki + 1] : null;
const strict = args.includes("--strict");
const file = args.find((a, i) => !a.startsWith("--") && (ki < 0 || i !== ki + 1));
if (!file || (ki >= 0 && !keyringPath)) {
  console.error("usage: node scripts/verify-license.mjs <license.json> [--keyring <keyring.json>] [--strict]");
  process.exit(2);
}

const keyring = parseKeyring(
  keyringPath ? readFileSync(keyringPath, "utf8") : process.env.LICENSE_PUBLIC_KEYS || readFileSync(KEYRING_FILE, "utf8"),
);
let doc;
try {
  doc = JSON.parse(readFileSync(file, "utf8"));
} catch (e) {
  console.error(`Not valid JSON: ${e.message}`);
  process.exit(1);
}
const check = await verifyLicenseSignature(doc, keyring);
if (!check.ok) {
  const why = {
    malformed: "the file is not a PineForge signed license",
    unknown_key: `key id "${doc?.signature?.kid}" is not in the keyring`,
    invalid_signature: "the signature does not match the license contents (altered or forged)",
  }[check.reason];
  console.log(`INVALID: ${why}.`);
  process.exit(1);
}
const l = doc.license;
const now = new Date();
const from = new Date(l.term.validFrom);
const until = new Date(l.term.validUntil);
const dates = now < from ? "not yet valid" : now > until ? "EXPIRED" : "within its term";
console.log(`Signature: valid (key ${check.kid})`);
console.log(`License:   ${l.id}`);
console.log(`Licensee:  ${l.licensee.company} (${l.licensee.country})`);
console.log(`Tier:      ${l.tier} / ${l.option}  scope ${JSON.stringify(l.scope)}`);
console.log(`Term:      ${l.term.validFrom} to ${l.term.validUntil} (${dates})`);
console.log(`Agreement: ${l.agreement.version} sha256 ${l.agreement.sha256}`);
if (l.mode === "test") {
  console.log("Mode:      TEST LICENSE: issued from a Stripe test-mode payment; not a commercial license.");
} else {
  console.log("Mode:      live");
}
console.log("Revocation: not visible offline; check the license id on the site's verify page.");
if (strict && (l.mode !== "live" || dates !== "within its term")) process.exit(3);
