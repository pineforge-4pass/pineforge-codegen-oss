import { test } from "node:test";
import assert from "node:assert/strict";
import {
  generateLicenseId,
  isLicenseId,
  licenseFingerprint,
  normalizeLicenseId,
  signLicense,
  verifyLicenseSignature,
  type LicensePayload,
  type PrivateJwk,
} from "../lib/license.ts";
import { addMonthsIso } from "../lib/dates.ts";
import { b64urlDecode } from "../lib/base64url.ts";
import { parseKeyring } from "../lib/license.ts";

async function keypair(kid: string) {
  const { privateKey } = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
  const jwk = (await crypto.subtle.exportKey("jwk", privateKey)) as JsonWebKey;
  const priv: PrivateJwk = { kty: "OKP", crv: "Ed25519", x: jwk.x as string, d: jwk.d as string, kid };
  return { priv, keyring: { keys: [{ kty: "OKP" as const, crv: "Ed25519" as const, x: priv.x, kid }] } };
}

const payload = (): LicensePayload => ({
  v: 1,
  id: generateLicenseId(),
  product: "pineforge-codegen",
  licensee: { company: "Example Research Ltd", country: "NZ" },
  tier: "team",
  option: "seats-5",
  scope: { seats: 5 },
  term: { months: 12, validFrom: "2026-10-04T00:00:00Z", validUntil: "2027-10-04T00:00:00Z" },
  issuedAt: "2026-10-04T00:00:00Z",
  mode: "test",
  agreement: { version: "draft", sha256: "0".repeat(64) },
  orderRef: "ord_test",
});

test("license ids: 80 random bits in Crockford base32", () => {
  const ids = new Set(Array.from({ length: 200 }, generateLicenseId));
  assert.equal(ids.size, 200);
  for (const id of ids) assert.ok(isLicenseId(id), id);
  assert.match([...ids][0], /^PFL-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$/);
  assert.equal(normalizeLicenseId(" pfl-abcd-efgh-jkmn-pq0o "), "PFL-ABCD-EFGH-JKMN-PQ00");
  assert.ok(!isLicenseId("PFL-ABCD"));
});

test("sign then verify; any change to the license or signature fails", async () => {
  const { priv, keyring } = await keypair("pfl-unit-1");
  const signed = await signLicense(payload(), priv);
  assert.deepEqual(await verifyLicenseSignature(signed, keyring), { ok: true, kid: "pfl-unit-1" });
  // Key order of the JSON does not matter: the signature covers the canonical form.
  const reordered = JSON.parse(JSON.stringify({ signature: signed.signature, license: { ...signed.license } }));
  assert.equal((await verifyLicenseSignature(reordered, keyring)).ok, true);

  const company = structuredClone(signed);
  company.license.licensee.company = "Someone Else Ltd";
  assert.deepEqual(await verifyLicenseSignature(company, keyring), { ok: false, reason: "invalid_signature" });
  const extended = structuredClone(signed);
  extended.license.term.validUntil = "2099-01-01T00:00:00Z";
  assert.deepEqual(await verifyLicenseSignature(extended, keyring), { ok: false, reason: "invalid_signature" });
  const sig = structuredClone(signed);
  sig.signature.value = (sig.signature.value[0] === "A" ? "B" : "A") + sig.signature.value.slice(1);
  assert.deepEqual(await verifyLicenseSignature(sig, keyring), { ok: false, reason: "invalid_signature" });
  const kid = structuredClone(signed);
  kid.signature.kid = "pfl-unknown";
  assert.deepEqual(await verifyLicenseSignature(kid, keyring), { ok: false, reason: "unknown_key" });
  assert.deepEqual(await verifyLicenseSignature({ license: {} }, keyring), { ok: false, reason: "malformed" });
  const other = await keypair("pfl-unit-1");
  assert.deepEqual(await verifyLicenseSignature(signed, other.keyring), { ok: false, reason: "invalid_signature" });
  assert.match(await licenseFingerprint(signed.signature.value), /^[0-9a-f]{4}( [0-9a-f]{4}){3}$/);
});

test("term arithmetic clamps the day", () => {
  assert.equal(addMonthsIso("2026-10-04T08:30:00Z", 12), "2027-10-04T08:30:00Z");
  assert.equal(addMonthsIso("2026-01-31T00:00:00Z", 1), "2026-02-28T00:00:00Z");
  assert.equal(addMonthsIso("2028-02-29T12:00:00Z", 12), "2029-02-28T12:00:00Z");
});

test("base64url accepts only the canonical encoding", () => {
  assert.deepEqual([...b64urlDecode("AQ")], [1]);
  assert.throws(() => b64urlDecode("AR"), /non-canonical/, "nonzero unused bits");
  assert.throws(() => b64urlDecode("AQ=="), /invalid/, "padding");
  assert.throws(() => b64urlDecode("A+/="), /invalid/);
});

test("a keyring with a duplicate kid is refused", () => {
  const k = { kty: "OKP", crv: "Ed25519", x: "2bIiI78i_XKQZ32PPGumido_8R5ad8vVdSToZ3gLN3s", kid: "pfl-a" };
  assert.equal(parseKeyring(JSON.stringify({ keys: [k] })).keys.length, 1);
  assert.throws(() => parseKeyring(JSON.stringify({ keys: [k, { ...k }] })), /duplicate kid/);
});
