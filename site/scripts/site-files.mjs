// Paths and readers shared by the build and check scripts.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const SITE = join(dirname(fileURLToPath(import.meta.url)), "..");
export const AGREEMENT_FILE = join(SITE, "legal", "commercial-license-agreement.md");
export const COMMERCE_FILE = join(SITE, "config", "commerce.json");
export const KEYRING_FILE = join(SITE, "keys", "license-public-keys.json");
export const AGREEMENT_MARKER = "DRAFT — requires review by counsel before go-live";

export function readCommerce() {
  return JSON.parse(readFileSync(COMMERCE_FILE, "utf8"));
}

/** The agreement's draft flag, version line and sha256 of its bytes. */
export async function agreementInfo() {
  const { createHash } = await import("node:crypto");
  const bytes = readFileSync(AGREEMENT_FILE);
  const text = bytes.toString("utf8");
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  // "Version: x", "**Version:** x", "**Version**: x"
  const m = text.match(/^[\s>*_]*Version[\s*_]*:[\s*_]*`?([^\s*`]+)/im);
  return {
    isDraft: text.includes(AGREEMENT_MARKER),
    version: m ? m[1] : `unversioned-${sha256.slice(0, 12)}`,
    sha256,
  };
}

/** KEY=VALUE pairs of site/.dev.vars (wrangler's local secrets file), if present. */
export function readDevVars() {
  const file = join(SITE, ".dev.vars");
  if (!existsSync(file)) return {};
  const out = {};
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!m) continue;
    out[m[1]] = m[2].trim().replace(/^(['"])(.*)\1$/, "$2").trim();
  }
  return out;
}
