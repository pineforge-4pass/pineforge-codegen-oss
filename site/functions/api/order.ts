// GET /api/order?session_id=<Stripe Checkout Session id>
//
// The order confirmation page polls this until the license is issued. The
// session id (from Stripe's redirect) is the capability; the answer carries
// no buyer name or email.
import type { Env } from "../../server/env.ts";
import { apiError, json } from "../../server/http.ts";
import { licenseByOrder, orderBySession, signedFromRow, type LicenseRow, type OrderRow } from "../../server/db.ts";
import type { LicensePayload } from "../../lib/license.ts";

type ApiStatus = "pending" | "issued" | "refunded" | "revoked" | "expired" | "failed" | "mismatch";

function apiStatus(order: OrderRow, license: LicenseRow | null): ApiStatus {
  switch (order.status) {
    case "paid":
      if (!license) return "pending";
      return license.status === "revoked" ? "revoked" : "issued";
    case "pending":
      return "pending";
    default:
      return order.status;
  }
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const sessionId = (new URL(request.url).searchParams.get("session_id") ?? "").trim();
  if (!sessionId) return apiError(400, "missing_session_id");
  if (sessionId.length > 255) return apiError(404, "not_found");

  try {
    const order = await orderBySession(env.DB, sessionId);
    if (!order) return apiError(404, "not_found");
    const row = await licenseByOrder(env.DB, order.id);
    const signed = row ? signedFromRow(row) : null;
    const payload: LicensePayload | null = signed?.license ?? null;

    return json(200, {
      status: apiStatus(order, row),
      order: {
        id: order.id,
        company: order.company,
        country: order.country,
        tier: order.tier,
        option: order.option_id,
        productName: order.product_name,
        scopeSummary: order.scope_summary,
        amountSubtotal: order.amount_subtotal,
        currency: order.currency,
        mode: order.livemode ? "live" : "test",
        createdAt: order.created_at,
        paidAt: order.paid_at,
      },
      license:
        row && payload
          ? {
              id: row.id,
              status: row.status,
              mode: row.mode,
              tier: payload.tier,
              option: payload.option,
              scope: payload.scope,
              validFrom: row.valid_from,
              validUntil: row.valid_until,
              issuedAt: row.issued_at,
              revokedAt: row.revoked_at,
              revokeReason: row.revoke_reason,
              certificateUrl: `/certificate/${row.id}`,
              verifyUrl: `/${order.locale}/verify/?id=${row.id}`,
            }
          : null,
      signedLicense: signed,
    });
  } catch (e) {
    console.error("[order] lookup failed:", e);
    return apiError(500, "server_error");
  }
};
