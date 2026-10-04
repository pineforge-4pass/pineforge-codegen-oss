// Trusted license verification keys: the keyring bundled with the site
// (keys/license-public-keys.json), or LICENSE_PUBLIC_KEYS, which REPLACES it.
// LICENSE_PUBLIC_KEYS exists for local development and tests; production
// never sets it.
import bundled from "../keys/license-public-keys.json";
import { parseKeyring, parsePrivateJwk, type Keyring, type PrivateJwk } from "../lib/license.ts";
import type { Env } from "./env.ts";

const BUNDLED: Keyring = parseKeyring(JSON.stringify(bundled));

/** Throws when LICENSE_PUBLIC_KEYS is set but is not a valid keyring (fail closed). */
export function trustedKeyring(env: Env): Keyring {
  const override = (env.LICENSE_PUBLIC_KEYS ?? "").trim();
  return override ? parseKeyring(override) : BUNDLED;
}

/** The Ed25519 private JWK in LICENSE_SIGNING_KEY; throws when missing or invalid. */
export function signingKey(env: Env): PrivateJwk {
  const raw = (env.LICENSE_SIGNING_KEY ?? "").trim();
  if (!raw) throw new Error("LICENSE_SIGNING_KEY is not set");
  return parsePrivateJwk(raw);
}
