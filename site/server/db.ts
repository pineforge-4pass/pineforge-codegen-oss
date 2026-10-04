// Row types and lookups of the D1 tables (migrations/0001_init.sql).
import type { LicensePayload, SignedLicense } from "../lib/license.ts";

export type OrderStatus = "pending" | "paid" | "refunded" | "expired" | "failed" | "mismatch";

export interface OrderRow {
  id: string;
  status: OrderStatus;
  company: string;
  country: string;
  buyer_name: string;
  buyer_email: string;
  reference: string | null;
  tier: string;
  option_id: string;
  term_months: number;
  product_name: string;
  scope_summary: string;
  scope_json: string;
  amount_subtotal: number;
  currency: string;
  locale: string;
  stripe_session_id: string | null;
  stripe_payment_intent: string | null;
  stripe_customer: string | null;
  stripe_invoice: string | null;
  amount_refunded: number;
  livemode: number;
  agreement_version: string;
  agreement_sha256: string;
  created_at: string;
  paid_at: string | null;
  refunded_at: string | null;
  updated_at: string;
}

export interface LicenseRow {
  id: string;
  order_id: string;
  kid: string;
  mode: "test" | "live";
  payload_json: string;
  signature: string;
  status: "active" | "revoked";
  issued_at: string;
  valid_from: string;
  valid_until: string;
  revoked_at: string | null;
  revoke_reason: string | null;
}

export function orderById(db: D1Database, id: string): Promise<OrderRow | null> {
  return db.prepare("SELECT * FROM orders WHERE id = ?1").bind(id).first<OrderRow>();
}

export function orderBySession(db: D1Database, sessionId: string): Promise<OrderRow | null> {
  return db.prepare("SELECT * FROM orders WHERE stripe_session_id = ?1").bind(sessionId).first<OrderRow>();
}

export function orderByPaymentIntent(db: D1Database, paymentIntent: string): Promise<OrderRow | null> {
  return db.prepare("SELECT * FROM orders WHERE stripe_payment_intent = ?1").bind(paymentIntent).first<OrderRow>();
}

export function licenseById(db: D1Database, id: string): Promise<LicenseRow | null> {
  return db.prepare("SELECT * FROM licenses WHERE id = ?1").bind(id).first<LicenseRow>();
}

export function licenseByOrder(db: D1Database, orderId: string): Promise<LicenseRow | null> {
  return db.prepare("SELECT * FROM licenses WHERE order_id = ?1").bind(orderId).first<LicenseRow>();
}

/** The stored license exactly as it was signed and handed to the buyer. */
export function signedFromRow(row: LicenseRow): SignedLicense {
  return {
    license: JSON.parse(row.payload_json) as LicensePayload,
    signature: { alg: "Ed25519", kid: row.kid, value: row.signature },
  };
}
