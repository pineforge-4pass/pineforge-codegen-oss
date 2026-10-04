// Stripe client for Workers: fetch-based HTTP client, API origin from
// STRIPE_API_BASE (default https://api.stripe.com; tests point it at a fake).
import Stripe from "stripe";
import type { Env } from "./env.ts";

export { Stripe };

export function stripeApiOrigin(env: Env): { host: string; port: number; protocol: "http" | "https" } {
  const url = new URL((env.STRIPE_API_BASE ?? "").trim() || "https://api.stripe.com");
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("STRIPE_API_BASE must be http(s)");
  const protocol = url.protocol === "http:" ? "http" : "https";
  const port = url.port ? Number(url.port) : protocol === "https" ? 443 : 80;
  return { host: url.hostname, port, protocol };
}

export function stripeClient(env: Env): Stripe {
  const key = (env.STRIPE_SECRET_KEY ?? "").trim();
  if (!key) throw new Error("STRIPE_SECRET_KEY is not set");
  const { host, port, protocol } = stripeApiOrigin(env);
  return new Stripe(key, {
    httpClient: Stripe.createFetchHttpClient(),
    host,
    port,
    protocol,
    maxNetworkRetries: 2,
    timeout: 20_000,
  });
}

/** The id of an expandable Stripe field (string id or expanded object), or null. */
export function idOf(x: string | { id: string } | null | undefined): string | null {
  if (!x) return null;
  return typeof x === "string" ? x : x.id;
}
