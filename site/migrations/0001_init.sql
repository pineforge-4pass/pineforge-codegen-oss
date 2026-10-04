-- Schema of the pineforge-license D1 database (Cloudflare Pages Functions).
-- Times are ISO 8601 UTC strings; amounts are integers in minor units.

-- One row per checkout attempt. The server prices it from config/commerce.json
-- and keeps a snapshot of what was sold (product name, scope, agreement).
CREATE TABLE orders (
  id TEXT PRIMARY KEY,                       -- ord_<random>
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'paid', 'refunded', 'expired', 'failed', 'mismatch')),
  company TEXT NOT NULL,
  country TEXT NOT NULL,
  buyer_name TEXT NOT NULL,
  buyer_email TEXT NOT NULL,
  reference TEXT,
  tier TEXT NOT NULL,
  option_id TEXT NOT NULL,
  term_months INTEGER NOT NULL,
  product_name TEXT NOT NULL,
  scope_summary TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  amount_subtotal INTEGER NOT NULL,
  currency TEXT NOT NULL,
  locale TEXT NOT NULL,
  stripe_session_id TEXT UNIQUE,
  stripe_payment_intent TEXT,
  stripe_customer TEXT,
  stripe_invoice TEXT,
  amount_refunded INTEGER NOT NULL DEFAULT 0,
  livemode INTEGER NOT NULL CHECK (livemode IN (0, 1)),
  agreement_version TEXT NOT NULL,
  agreement_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  paid_at TEXT,
  refunded_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX orders_payment_intent ON orders (stripe_payment_intent);

-- At most one license per order (order_id UNIQUE). payload_json is the
-- canonical JSON of the signed license object, byte for byte.
CREATE TABLE licenses (
  id TEXT PRIMARY KEY,                       -- PFL-XXXX-XXXX-XXXX-XXXX
  order_id TEXT NOT NULL UNIQUE REFERENCES orders (id),
  kid TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('test', 'live')),
  payload_json TEXT NOT NULL,
  signature TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  issued_at TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  revoked_at TEXT,
  revoke_reason TEXT
);

-- Stripe event ids already processed (recorded after processing succeeds).
CREATE TABLE stripe_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  livemode INTEGER NOT NULL CHECK (livemode IN (0, 1)),
  received_at TEXT NOT NULL
);

-- "Talk to us" quote requests, stored before the notification email is sent.
CREATE TABLE quotes (
  id TEXT PRIMARY KEY,                       -- quo_<random>
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  company TEXT NOT NULL,
  use_case TEXT NOT NULL,
  seats TEXT,
  aum TEXT,
  deployment TEXT,
  message TEXT NOT NULL,
  locale TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX quotes_created ON quotes (created_at);

-- Every email the Functions tried to send, with the provider's answer.
CREATE TABLE email_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  kind TEXT NOT NULL,                        -- license | sale | quote | alert
  related_id TEXT,                           -- order, license or quote id
  provider_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
  error TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX email_log_related ON email_log (related_id);

-- Fixed-window request counters, keyed "<scope>:<client ip>".
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,             -- unix ms at the start of the window
  count INTEGER NOT NULL
);
CREATE INDEX rate_limits_window ON rate_limits (window_start);
