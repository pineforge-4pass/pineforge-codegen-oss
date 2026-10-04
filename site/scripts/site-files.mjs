// Paths and readers shared by the build and check scripts.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const SITE = join(dirname(fileURLToPath(import.meta.url)), "..");
export const AGREEMENT_FILE = join(SITE, "legal", "commercial-license-agreement.md");
export const COMMERCE_FILE = join(SITE, "config", "commerce.json");
export const KEYRING_FILE = join(SITE, "keys", "license-public-keys.json");

export function readCommerce() {
  return JSON.parse(readFileSync(COMMERCE_FILE, "utf8"));
}

/** The agreement's draft status (lib/agreement-status.ts), version line and sha256 of its bytes. */
export async function agreementInfo() {
  const { createHash } = await import("node:crypto");
  const { agreementStatus } = await import("../lib/agreement-status.ts");
  const bytes = readFileSync(AGREEMENT_FILE);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const { agreementApprovedSha256 = null } = readCommerce();
  const status = agreementStatus(bytes.toString("utf8"), { sha256, approvedSha256: agreementApprovedSha256 });
  return {
    isDraft: status.isDraft,
    draftReasons: status.reasons,
    version: status.version ?? `unversioned-${sha256.slice(0, 12)}`,
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
