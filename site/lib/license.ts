// Signed license format: an Ed25519 signature (WebCrypto) over the canonical
// JSON of the `license` object. Runs in browsers, Cloudflare Workers and
// Node 22. Keys are passed in; nothing here reads the environment.
import { canonicalize } from "./canonical-json.ts";
import { b64urlDecode, b64urlEncode } from "./base64url.ts";

export type LicenseMode = "test" | "live";

export interface LicenseScope {
  seats?: number;
  aumBandUsd?: [number, number];
  products?: number;
  endUsers?: number;
}

export interface LicensePayload {
  v: 1;
  id: string;
  product: "pineforge-codegen";
  licensee: { company: string; country: string };
  tier: string;
  option: string;
  scope: LicenseScope;
  term: { months: number; validFrom: string; validUntil: string };
  issuedAt: string;
  mode: LicenseMode;
  agreement: { version: string; sha256: string };
  orderRef: string;
}

export interface SignedLicense {
  license: LicensePayload;
  signature: { alg: "Ed25519"; kid: string; value: string };
}

export interface PublicJwk {
  kty: "OKP";
  crv: "Ed25519";
  x: string;
  kid: string;
  alg?: string;
  use?: string;
}

export interface PrivateJwk extends PublicJwk {
  d: string;
}

export interface Keyring {
  keys: PublicJwk[];
}

export type SigCheck =
  | { ok: true; kid: string }
  | { ok: false; reason: "malformed" | "unknown_key" | "invalid_signature" };

// Crockford base32 (no I, L, O, U).
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const ID_RE = /^PFL-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$/;

/** "PFL-XXXX-XXXX-XXXX-XXXX": 16 Crockford base32 characters = 80 random bits. */
export function generateLicenseId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  let out = "";
  let acc = 0;
  let bits = 0;
  for (const b of bytes) {
    acc = (acc << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += CROCKFORD[(acc >>> (bits - 5)) & 31];
      bits -= 5;
    }
    acc &= (1 << bits) - 1;
  }
  return "PFL-" + (out.match(/.{4}/g) as string[]).join("-");
}

/** Uppercases and maps the Crockford look-alikes (O to 0, I and L to 1). */
export function normalizeLicenseId(s: string): string {
  const up = s.trim().toUpperCase();
  if (!up.startsWith("PFL-")) return up;
  return "PFL-" + up.slice(4).replace(/O/g, "0").replace(/[IL]/g, "1");
}

export function isLicenseId(s: string): boolean {
  return ID_RE.test(normalizeLicenseId(s));
}

const isObj = (x: unknown): x is Record<string, unknown> =>
  typeof x === "object" && x !== null && !Array.isArray(x);
const isStr = (x: unknown): x is string => typeof x === "string";
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** Strict shape check of a signed license; returns null when anything is off. */
export function parseSignedLicense(x: unknown): SignedLicense | null {
  if (!isObj(x) || !isObj(x.license) || !isObj(x.signature)) return null;
  const l = x.license;
  const s = x.signature;
  if (l.v !== 1 || !isStr(l.id) || !ID_RE.test(l.id) || l.product !== "pineforge-codegen") return null;
  if (!isObj(l.licensee) || !isStr(l.licensee.company) || !isStr(l.licensee.country)) return null;
  if (!isStr(l.tier) || !isStr(l.option) || !isObj(l.scope)) return null;
  if (!isObj(l.term) || !isNum(l.term.months) || !isStr(l.term.validFrom) || !isStr(l.term.validUntil)) return null;
  if (!isStr(l.issuedAt) || (l.mode !== "test" && l.mode !== "live") || !isStr(l.orderRef)) return null;
  if (!isObj(l.agreement) || !isStr(l.agreement.version) || !isStr(l.agreement.sha256)) return null;
  if (s.alg !== "Ed25519" || !isStr(s.kid) || !isStr(s.value)) return null;
  return x as unknown as SignedLicense;
}

function utf8(s: string): Uint8Array<ArrayBuffer> {
  const enc = new TextEncoder().encode(s);
  const out = new Uint8Array(new ArrayBuffer(enc.length));
  out.set(enc);
  return out;
}

export async function signLicense(payload: LicensePayload, key: PrivateJwk): Promise<SignedLicense> {
  const k = await crypto.subtle.importKey(
    "jwk",
    { kty: "OKP", crv: "Ed25519", x: key.x, d: key.d },
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, k, utf8(canonicalize(payload))));
  return { license: payload, signature: { alg: "Ed25519", kid: key.kid, value: b64urlEncode(sig) } };
}

export async function verifyLicenseSignature(signed: unknown, keyring: Keyring): Promise<SigCheck> {
  const parsed = parseSignedLicense(signed);
  if (!parsed) return { ok: false, reason: "malformed" };
  const jwk = keyring.keys.find((k) => k.kid === parsed.signature.kid);
  if (!jwk) return { ok: false, reason: "unknown_key" };
  let sig: Uint8Array<ArrayBuffer>;
  try {
    sig = b64urlDecode(parsed.signature.value);
  } catch {
    return { ok: false, reason: "invalid_signature" };
  }
  if (sig.length !== 64) return { ok: false, reason: "invalid_signature" };
  try {
    const k = await crypto.subtle.importKey(
      "jwk",
      { kty: "OKP", crv: "Ed25519", x: jwk.x },
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const ok = await crypto.subtle.verify({ name: "Ed25519" }, k, sig, utf8(canonicalize(parsed.license)));
    return ok ? { ok: true, kid: jwk.kid } : { ok: false, reason: "invalid_signature" };
  } catch {
    return { ok: false, reason: "invalid_signature" };
  }
}

function checkPublicJwk(k: unknown): PublicJwk {
  if (!isObj(k) || k.kty !== "OKP" || k.crv !== "Ed25519" || !isStr(k.x) || !isStr(k.kid) || !k.kid) {
    throw new Error("not an Ed25519 public JWK with a kid");
  }
  return k as unknown as PublicJwk;
}

/** Parses the LICENSE_SIGNING_KEY secret: an Ed25519 private JWK with a kid. */
export function parsePrivateJwk(json: string): PrivateJwk {
  const k: unknown = JSON.parse(json);
  checkPublicJwk(k);
  if (!isObj(k) || !isStr(k.d) || !k.d) throw new Error("not an Ed25519 private JWK");
  return k as unknown as PrivateJwk;
}

/** Parses a keyring `{"keys":[<public JWK>, ...]}`. */
export function parseKeyring(json: string): Keyring {
  const r: unknown = JSON.parse(json);
  if (!isObj(r) || !Array.isArray(r.keys)) throw new Error("keyring must be {\"keys\": [...]}");
  return { keys: r.keys.map(checkPublicJwk) };
}

/** First 64 bits of sha256(signature bytes) in hex, grouped by four: "ab12 cd34 ef56 7890". */
export async function licenseFingerprint(signatureValue: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", b64urlDecode(signatureValue)));
  const hex = Array.from(digest.slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
  return (hex.match(/.{4}/g) as string[]).join(" ");
}
