// Fixed-window rate limit backed by D1 (table rate_limits). One atomic upsert
// per request: the counter restarts when the window it belongs to changes.

export interface RateLimitRule {
  scope: string;
  limit: number;
  windowMs: number;
}

export const CHECKOUT_LIMIT: RateLimitRule = { scope: "checkout", limit: 30, windowMs: 10 * 60 * 1000 };
export const QUOTE_LIMIT: RateLimitRule = { scope: "quote", limit: 10, windowMs: 15 * 60 * 1000 };

/** Counts this request; true while the client is within the rule's limit. */
export async function allowRequest(db: D1Database, rule: RateLimitRule, ip: string, now = Date.now()): Promise<boolean> {
  const windowStart = Math.floor(now / rule.windowMs) * rule.windowMs;
  const row = await db
    .prepare(
      `INSERT INTO rate_limits (key, window_start, count) VALUES (?1, ?2, 1)
       ON CONFLICT (key) DO UPDATE SET
         count = CASE WHEN rate_limits.window_start = excluded.window_start THEN rate_limits.count + 1 ELSE 1 END,
         window_start = excluded.window_start
       RETURNING count`,
    )
    .bind(`${rule.scope}:${ip}`, windowStart)
    .first<{ count: number }>();
  // Occasionally drop counters of windows that ended long ago.
  if (Math.random() < 0.02) {
    try {
      await db.prepare("DELETE FROM rate_limits WHERE window_start < ?1").bind(now - 24 * 60 * 60 * 1000).run();
    } catch (e) {
      console.error("[rate-limit] prune failed:", e);
    }
  }
  return (row?.count ?? 1) <= rule.limit;
}
