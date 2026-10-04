// The printable license certificate (GET /certificate/<id>): a standalone HTML
// page with the site's tokens inline, its night edition and print styles.
// Copy comes from the `certificate` namespace of messages/en.json; every
// value is escaped.
import messages from "../messages/en.json";
import { isoDate } from "../lib/dates.ts";
import type { LicensePayload } from "../lib/license.ts";
import { escapeHtml, fill } from "./http.ts";
import { scopeText } from "./views.ts";

type CertificateCopy = Record<
  | "docTitle" | "heading" | "product" | "productValue" | "licensee" | "country" | "licenseId" | "tier" | "scope"
  | "term" | "termValue" | "issued" | "agreement" | "agreementValue" | "agreementHash" | "key" | "fingerprint"
  | "status" | "statusActive" | "statusRevoked" | "statusExpired" | "revokedBanner" | "testBanner"
  | "verifyHeading" | "verifyOnline" | "verifyOffline" | "print" | "notFoundTitle" | "notFoundBody"
  | "tierTeam" | "tierFund" | "tierOem" | "reasonRefund" | "reasonOther",
  string
>;

const t = (messages as unknown as { certificate: CertificateCopy }).certificate;

export type CertificateStatus = "active" | "revoked" | "expired";

export interface CertificateData {
  payload: LicensePayload;
  kid: string;
  fingerprint: string;
  status: CertificateStatus;
  revokedAt: string | null;
  revokeReason: string | null;
  siteUrl: string;
  locale: string;
}

/** Fills a catalog string whose placeholders take ready-made HTML; the literal text is escaped. */
function fillHtml(template: string, html: Record<string, string>): string {
  return template
    .split(/(\{\w+\})/)
    .map((part) => {
      const m = /^\{(\w+)\}$/.exec(part);
      return m && m[1] in html ? html[m[1]] : escapeHtml(part);
    })
    .join("");
}

const link = (href: string, text = href) => `<a href="${escapeHtml(href)}">${escapeHtml(text)}</a>`;

function tierLabel(tier: string): string {
  if (tier === "team") return t.tierTeam;
  if (tier === "fund") return t.tierFund;
  if (tier === "oem") return t.tierOem;
  return tier;
}

// The site's self-hosted IBM Plex (public/fonts, latin subset); the system
// stacks below stay as fallbacks.
const STYLE = `
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 400; font-display: swap; src: url("/fonts/ibm-plex-sans-latin-400-normal.woff2") format("woff2"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 500; font-display: swap; src: url("/fonts/ibm-plex-sans-latin-500-normal.woff2") format("woff2"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 600; font-display: swap; src: url("/fonts/ibm-plex-sans-latin-600-normal.woff2") format("woff2"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400; font-display: swap; src: url("/fonts/ibm-plex-mono-latin-400-normal.woff2") format("woff2"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
:root {
  --paper: #ecefe9; --ink: #0f2b21; --ink-2: #46594f; --hair: #c9d1ca; --rule: #7d8c84; --field: #e2e7e0;
  --accent: #e8a33d; --accent-text: #8a4f00; --on-accent: #0f2b21;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0b1813; --ink: #d9d5c7; --ink-2: #9daaa1; --rule: #5e7166; --hair: #22342b; --field: #0f2019;
    --accent: #f0b24e; --accent-text: #f0b24e; --on-accent: #0b1813;
  }
}
*, *::before, *::after { box-sizing: border-box; border-radius: 0; }
html { background: var(--paper); color: var(--ink); -webkit-text-size-adjust: 100%; }
body {
  margin: 0; font-size: 16px; line-height: 1.55;
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.mono { font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
.wrap { max-width: 52rem; margin: 0 auto; padding-left: 1.5rem; padding-right: 1.5rem; }
.mast { border-bottom: 1px solid var(--hair); }
.mast p { margin: 0; padding: 1.25rem 0; font-weight: 600; }
.mast a { color: var(--ink); text-decoration: none; }
.mast a:hover { text-decoration: underline; }
main { padding-top: 3rem; padding-bottom: 4rem; }
h1 { font-size: 2rem; line-height: 1.2; font-weight: 600; margin: 0 0 0.75rem; letter-spacing: -0.01em; }
.idline { margin: 0 0 2.5rem; font-size: 1.125rem; color: var(--ink-2); }
.idline .mono { color: var(--ink); font-size: 1.25rem; font-weight: 500; }
.banner { border: 2px solid var(--ink); padding: 1rem 1.25rem; margin: 0 0 2rem; font-weight: 600; }
.banner-test { border-style: dashed; }
dl { margin: 0; border-top: 1px solid var(--rule); }
dl > div { display: grid; grid-template-columns: 13rem 1fr; gap: 1.5rem; padding: 0.75rem 0; border-bottom: 1px solid var(--hair); }
dt { color: var(--ink-2); }
dd { margin: 0; overflow-wrap: anywhere; }
h2 { font-size: 1.25rem; line-height: 1.3; font-weight: 600; margin: 3rem 0 0.75rem; }
.how p { margin: 0 0 0.75rem; max-width: 44rem; }
a { color: var(--ink); text-decoration: underline; text-underline-offset: 0.15em; }
a:focus-visible, button:focus-visible { outline: 3px solid var(--ink); outline-offset: 3px; }
.actions { margin: 2.5rem 0 0; }
.primary {
  min-height: 44px; min-width: 44px; padding: 0.625rem 1.25rem; font: inherit; font-weight: 600; cursor: pointer;
  background: var(--accent); color: var(--on-accent); border: 2px solid var(--accent);
}
@media (prefers-color-scheme: dark) {
  /* At night the primary action is an amber outline. */
  .primary { background: transparent; color: var(--accent-text); }
}
@media (max-width: 40rem) {
  dl > div { grid-template-columns: 1fr; gap: 0.125rem; }
  h1 { font-size: 1.625rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
@media print {
  :root { --paper: #ffffff; --ink: #000000; --ink-2: #333333; --hair: #bbbbbb; --rule: #666666; }
  html, body { background: #ffffff; color: #000000; }
  .mast, .actions { display: none; }
  main { padding-top: 0; padding-bottom: 0; }
  .wrap { max-width: none; padding-left: 0; padding-right: 0; }
  a { color: #000000; text-decoration: none; }
  dl > div { break-inside: avoid; }
  @page { margin: 18mm; }
}
`;

function page(opts: { title: string; nonce: string; locale: string; main: string }): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escapeHtml(opts.title)}</title>
<style nonce="${opts.nonce}">${STYLE}</style>
</head>
<body>
<header class="mast"><div class="wrap"><p><a href="/${escapeHtml(opts.locale)}/">PineForge Codegen</a></p></div></header>
${opts.main}
<script nonce="${opts.nonce}">(function(){var b=document.getElementById("print");if(b)b.addEventListener("click",function(){window.print();});})();</script>
</body>
</html>
`;
}

function nonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}

function headers(n: string): Record<string, string> {
  return {
    "Content-Security-Policy": [
      "default-src 'none'",
      `style-src 'nonce-${n}'`,
      `script-src 'nonce-${n}'`,
      "img-src 'self'",
      "font-src 'self'",
      "base-uri 'none'",
      "form-action 'none'",
      "frame-ancestors 'none'",
    ].join("; "),
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex",
  };
}

export function renderCertificate(d: CertificateData): { body: string; headers: Record<string, string> } {
  const p = d.payload;
  const n = nonce();
  const statusLabel = d.status === "revoked" ? t.statusRevoked : d.status === "expired" ? t.statusExpired : t.statusActive;
  const row = (label: string, value: string, mono = false) =>
    `<div><dt>${escapeHtml(label)}</dt><dd${mono ? ' class="mono"' : ""}>${value}</dd></div>`;
  const verifyUrl = `${d.siteUrl}/${d.locale}/verify/?id=${p.id}`;
  const keysUrl = `${d.siteUrl}/.well-known/pineforge-license-keys.json`;

  const banners: string[] = [];
  if (d.status === "revoked") {
    const reason = d.revokeReason === "refund" ? t.reasonRefund : t.reasonOther;
    banners.push(
      `<p class="banner" data-testid="revoked-banner">${escapeHtml(
        fill(t.revokedBanner, { date: d.revokedAt ? isoDate(d.revokedAt) : "", reason }),
      )}</p>`,
    );
  }
  if (p.mode === "test") {
    banners.push(`<p class="banner banner-test" data-testid="test-banner">${escapeHtml(t.testBanner)}</p>`);
  }

  const main = `<main id="main" class="wrap" data-testid="certificate" data-status="${d.status}" data-mode="${p.mode === "live" ? "live" : "test"}">
${banners.join("\n")}
<h1>${escapeHtml(t.heading)}</h1>
<p class="idline">${escapeHtml(t.licenseId)} <span class="mono" data-testid="license-id">${escapeHtml(p.id)}</span></p>
<dl>
${row(t.licensee, escapeHtml(p.licensee.company))}
${row(t.country, escapeHtml(p.licensee.country))}
${row(t.product, escapeHtml(t.productValue))}
${row(t.tier, escapeHtml(tierLabel(p.tier)))}
${row(t.scope, escapeHtml(scopeText(p.tier, p.scope)))}
${row(t.term, escapeHtml(fill(t.termValue, { from: isoDate(p.term.validFrom), until: isoDate(p.term.validUntil), months: p.term.months })), true)}
${row(t.issued, escapeHtml(p.issuedAt), true)}
${row(t.agreement, escapeHtml(fill(t.agreementValue, { version: p.agreement.version })))}
${row(t.agreementHash, escapeHtml(p.agreement.sha256), true)}
${row(t.key, escapeHtml(d.kid), true)}
${row(t.fingerprint, escapeHtml(d.fingerprint), true)}
${row(t.status, `<span data-testid="certificate-status">${escapeHtml(statusLabel)}</span>`)}
</dl>
<section class="how" aria-labelledby="how-heading">
<h2 id="how-heading">${escapeHtml(t.verifyHeading)}</h2>
<p>${fillHtml(t.verifyOnline, { url: link(verifyUrl) })}</p>
<p>${fillHtml(t.verifyOffline, { keysUrl: link(keysUrl) })}</p>
</section>
<p class="actions"><button type="button" id="print" class="primary">${escapeHtml(t.print)}</button></p>
</main>`;

  return {
    body: page({ title: fill(t.docTitle, { id: p.id }), nonce: n, locale: d.locale, main }),
    headers: headers(n),
  };
}

export function renderNotFound(id: string, contactEmail: string, locale: string): { body: string; headers: Record<string, string> } {
  const n = nonce();
  const main = `<main id="main" class="wrap" data-testid="certificate-not-found">
<h1>${escapeHtml(t.notFoundTitle)}</h1>
<p>${fillHtml(t.notFoundBody, {
    id: `<span class="mono">${escapeHtml(id)}</span>`,
    email: link(`mailto:${contactEmail}`, contactEmail),
  })}</p>
</main>`;
  return { body: page({ title: t.notFoundTitle, nonce: n, locale, main }), headers: headers(n) };
}
