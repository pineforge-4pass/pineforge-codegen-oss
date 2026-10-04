// Trusted license verification keys: the keyring bundled with the site
// (keys/license-public-keys.json), or LICENSE_PUBLIC_KEYS, which REPLACES it.
// LICENSE_PUBLIC_KEYS exists for local development, tests and previews;
// production never sets it, and a live deployment ignores it.
import bundled from "../keys/license-public-keys.json";
import { parseKeyring, parsePrivateJwk, type Keyring, type PrivateJwk } from "../lib/license.ts";
import { testOverride, type Env } from "./env.ts";

const BUNDLED: Keyring = parseKeyring(JSON.stringify(bundled));

/** Throws when LICENSE_PUBLIC_KEYS is set but is not a valid keyring (fail closed). */
export function trustedKeyring(env: Env): Keyring {
  const override = testOverride(env, "LICENSE_PUBLIC_KEYS");
  return override ? parseKeyring(override) : BUNDLED;
}

/** True when `kid` names a key of the BUNDLED (production) keyring, whatever LICENSE_PUBLIC_KEYS says. */
export function isProductionKid(kid: string, x?: string): boolean {
  return BUNDLED.keys.some((k) => k.kid === kid || (x !== undefined && k.x === x));
}

/** The Ed25519 private JWK in LICENSE_SIGNING_KEY; throws when missing or invalid. */
export function signingKey(env: Env): PrivateJwk {
  const raw = (env.LICENSE_SIGNING_KEY ?? "").trim();
  if (!raw) throw new Error("LICENSE_SIGNING_KEY is not set");
  return parsePrivateJwk(raw);
}
