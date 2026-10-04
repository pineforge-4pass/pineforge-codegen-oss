// GET /api/verify?id=<license id>   and   POST /api/verify (body: signed license JSON)
//
// Order of checks: malformed -> unknown_key -> invalid_signature -> not_found
// (not in D1, or the stored payload differs) -> revoked -> not_yet_valid /
// expired -> test_mode (a test license on a live deployment) -> valid.
// `license` is null unless the signature verified (POST) or the id is in D1
// (GET). Public fields only; never the buyer's email.
import { canonicalize } from "../../lib/canonical-json.ts";
import { isLicenseId, normalizeLicenseId, parseSignedLicense, verifyLicenseSignature } from "../../lib/license.ts";
import type { LicensePayload } from "../../lib/license.ts";
import { deploymentMode, type Env } from "../../server/env.ts";
import { BodyError, apiError, json, readJson } from "../../server/http.ts";
import { licenseById, signedFromRow, type LicenseRow } from "../../server/db.ts";
import { trustedKeyring } from "../../server/keyring.ts";
import { publicLicense, termState, type PublicLicense } from "../../server/views.ts";

type VerifyStatus =
  | "valid"
  | "revoked"
  | "expired"
  | "not_yet_valid"
  | "test_mode"
  | "invalid_signature"
  | "unknown_key"
  | "not_found"
  | "malformed";

interface VerifyAnswer {
  valid: boolean;
  status: VerifyStatus;
  mode: "test" | "live" | null;
  license: PublicLicense | null;
}

const answer = (status: VerifyStatus, license: PublicLicense | null): Response =>
  json(200, { valid: status === "valid", status, mode: license?.mode ?? null, license } satisfies VerifyAnswer);

/** The checks after the signature: D1 state, term, mode. */
function lifecycle(env: Env, payload: LicensePayload, row: LicenseRow): VerifyStatus {
  if (row.status === "revoked") return "revoked";
  const term = termState(payload.term.validFrom, payload.term.validUntil);
  if (term !== "current") return term;
  if (payload.mode === "test" && deploymentMode(env) === "live") return "test_mode";
  return "valid";
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const raw = (new URL(request.url).searchParams.get("id") ?? "").trim();
  if (!raw) return apiError(400, "missing_id");
  if (raw.length > 64 || !isLicenseId(raw)) return answer("malformed", null);
  try {
    const row = await licenseById(env.DB, normalizeLicenseId(raw));
    if (!row) return answer("not_found", null);
    const signed = signedFromRow(row);
    const view = publicLicense(signed.license, row.kid, row);
    const sig = await verifyLicenseSignature(signed, trustedKeyring(env));
    if (!sig.ok) return answer(sig.reason, view);
    return answer(lifecycle(env, signed.license, row), view);
  } catch (e) {
    console.error("[verify] GET failed:", e);
    return apiError(500, "server_error");
  }
};

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  let body: unknown;
  try {
    body = await readJson(request, 64 * 1024);
  } catch (e) {
    if (!(e instanceof BodyError)) console.error("[verify] reading the body failed:", e);
    return answer("malformed", null);
  }
  const signed = parseSignedLicense(body);
  if (!signed) return answer("malformed", null);
  try {
    const sig = await verifyLicenseSignature(signed, trustedKeyring(env));
    if (!sig.ok) return answer(sig.reason, null);
    const row = await licenseById(env.DB, signed.license.id);
    const known = row !== null && row.payload_json === canonicalize(signed.license) && row.signature === signed.signature.value;
    const view = publicLicense(signed.license, signed.signature.kid, known ? row : null);
    if (!known || !row) return answer("not_found", view);
    return answer(lifecycle(env, signed.license, row), view);
  } catch (e) {
    console.error("[verify] POST failed:", e);
    return apiError(500, "server_error");
  }
};
