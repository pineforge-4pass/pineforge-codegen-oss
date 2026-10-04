// GET /certificate/<license id>: the printable license certificate (HTML),
// or an HTML 404 page for an unknown or malformed id.
import { commerce } from "../../lib/commerce-config.ts";
import { isLicenseId, licenseFingerprint, normalizeLicenseId } from "../../lib/license.ts";
import { safeLocale, siteUrl, type Env } from "../../server/env.ts";
import { html } from "../../server/http.ts";
import { licenseById, orderById, signedFromRow } from "../../server/db.ts";
import { renderCertificate, renderNotFound, type CertificateStatus } from "../../server/certificate.ts";
import { termState } from "../../server/views.ts";

export const onRequestGet: PagesFunction<Env> = async ({ request, env, params }) => {
  const raw = (Array.isArray(params.id) ? params.id.join("/") : String(params.id ?? "")).trim();
  const site = siteUrl(env, request);
  const locale = safeLocale(undefined);
  const notFound = () => {
    const page = renderNotFound(raw.slice(0, 64), commerce.seller.contactEmail, site, locale);
    return html(404, page.body, page.headers);
  };
  if (!raw || raw.length > 64 || !isLicenseId(raw)) return notFound();

  try {
    const row = await licenseById(env.DB, normalizeLicenseId(raw));
    if (!row) return notFound();
    const signed = signedFromRow(row);
    const order = await orderById(env.DB, row.order_id);
    const status: CertificateStatus =
      row.status === "revoked"
        ? "revoked"
        : termState(signed.license.term.validFrom, signed.license.term.validUntil) === "expired"
          ? "expired"
          : "active";
    const page = renderCertificate({
      payload: signed.license,
      kid: row.kid,
      fingerprint: await licenseFingerprint(row.signature),
      status,
      revokedAt: row.revoked_at,
      revokeReason: row.revoke_reason,
      siteUrl: site,
      locale: safeLocale(order?.locale),
    });
    return html(200, page.body, page.headers);
  } catch (e) {
    console.error("[certificate] render failed:", e);
    return html(500, "<!doctype html><html lang=\"en\"><title>Error</title><p>Server error.</p></html>");
  }
};
