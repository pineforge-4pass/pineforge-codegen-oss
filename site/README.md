# PineForge Codegen commercial-license site

The self-serve site where an organization buys a commercial license for
PineForge Codegen (the PineScript v6 to C++ transpiler in this repository),
receives a signed license certificate, and where anyone can verify one.

It is its own small project: Next.js 16 pages exported as static files, plus
Cloudflare Pages Functions for the API, the Stripe webhook and the
certificate, a D1 database, Stripe Checkout for payment and Resend for email.
Nothing here is part of the Python package, the Pyodide npm package or the
codegen parity gate.

The [LICENSE](../LICENSE) is the controlling text for every use of the
software. The site summarises it and quotes it verbatim; it is not legal
advice.

## Status: preview, test mode only

- Prices in `config/commerce.json` are placeholders (`pricesArePlaceholders:
  true`), and the site says so on every page.
- The selling entity is not named yet (`seller.legalName: null`).
- The Commercial License Agreement in `legal/commercial-license-agreement.md`
  is a draft. It counts as final only when all of these hold
  (`lib/agreement-status.ts`, shared by the build guard and the pages):
  - the draft marker is gone, in any case or form ("DRAFT", "Draft —",
    "draft - requires review", ...), and no line says "DRAFT";
  - the draft notice paragraph is gone ("no one can accept it", "this notice
    is removed");
  - a line exactly `Status: final` is present;
  - neither the `Version:` line nor its value (also on the next line) says
    "draft";
  - no bracket placeholder is left: any `[...]` other than a Markdown link,
    reference or footnote, and any bracket text in capitals or naming the
    owner or counsel, in every form. All-caps link text such as
    `[LICENSE](url)` is refused too, so word such links differently
    (`[the license text](url)`).

  Anything else reads as draft.

While any of those holds, the site shows a preview notice on every page and
live payments are refused twice over: `npm run build` fails when a live
Stripe key is in its environment (or in `.dev.vars`), and at run time
`/api/checkout` answers 503 and the webhook never issues a license from a
live-mode event. See "Go-live checklist".

## Quick start

Node 22.18 or newer (the scripts import TypeScript modules directly).

```bash
cd site
npm ci
npm run dev        # pages only, http://localhost:3000/en/ (the API needs wrangler)
npm run check      # types, config schema, LICENSE quotes, i18n catalogs, unit tests
npm run build      # live-payment guard, build info, static export to out/
npm run preview    # build, then serve out/ and functions/ with wrangler pages dev
npm run e2e        # the end-to-end suite (below)
```

`npm run preview` needs local bindings. Create `site/.dev.vars` (ignored by
git; never commit it) with test-mode values, for example:

```ini
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
LICENSE_SIGNING_KEY={"kty":"OKP","crv":"Ed25519","x":"...","d":"...","kid":"pfl-dev-1"}
LICENSE_PUBLIC_KEYS={"keys":[{"kty":"OKP","crv":"Ed25519","x":"...","kid":"pfl-dev-1"}]}
RESEND_API_KEY=re_...
SITE_URL=http://localhost:8788
```

Generate a development key pair with `node scripts/generate-signing-key.mjs
pfl-dev-1` and apply the schema with `npm run d1:migrate:local`.

## Testing

### End-to-end: the proof

`npm run e2e` (`e2e/run.mjs`) drives the real site through a browser and
over HTTP. It:

1. picks free ports, generates a throwaway Ed25519 key pair and webhook
   secret for this run only;
2. builds the site if `out/` is missing, applies the D1 migrations to a fresh
   local database directory;
3. starts two local fakes and three `wrangler pages dev out` instances: the
   test-mode site (`TEST_EMAIL_ALLOWLIST=@example.com`), one holding a
   live-looking Stripe key (the live-payment guard, and a live-mode event on
   a blocked deployment), and a test-mode one whose signing key the keyring
   does not trust (the issuance-failure alert);
4. runs Playwright (`e2e/playwright.config.mjs`, specs in `e2e/specs/`),
   tears everything down and exits with Playwright's code.

**The only mocks are the two third parties**, which a test run cannot reach:

- `e2e/fakes/stripe.mjs` speaks the part of the Stripe API the site uses
  (`POST /v1/checkout/sessions`, `GET /v1/checkout/sessions/:id`), serves a
  look-alike hosted checkout page, and posts webhook events signed exactly as
  Stripe signs them. Card `4242 4242 4242 4242` pays; `4000 0000 0000 0002`
  is declined; `4000 0000 0000 0077` pays but delivers its webhook after the
  redirect. Control endpoints refund a payment (`charge.refunded`, the charge
  carrying the PaymentIntent's metadata as on Stripe), open and close
  disputes (`charge.dispute.*`) and redeliver an event.
- `e2e/fakes/resend.mjs` accepts `POST /emails` like Resend and keeps the
  messages for the tests to read. Nothing is sent.

Everything else (pages, Functions, D1, signing, verification) is the real
code. One spec reaches past the interface, and says so: a deployment with a
live key refuses checkout, so the live-mode event spec seeds that
deployment's order with `wrangler d1 execute` and reads its `email_log` (its
email goes nowhere: a live deployment ignores `RESEND_API_BASE`). The suite covers the decision guide (with and without JavaScript), the
plans and checkout (desktop and a 390 px phone), a purchase from plans to
certificate, the emails and their attached license (checked with
`scripts/verify-license.mjs`), verification of genuine and tampered licenses,
webhook signature rejection and the other checkout events, refunds and
revocation, the live-payment guard, the quote form, headers and redirects,
and axe-core accessibility checks of every page in both colour schemes.
Screenshots land in `e2e/artifacts/screens/` and wrangler logs in
`e2e/artifacts/run/` (both ignored by git).

Useful knobs: `npm run e2e -- specs/purchase.spec.mjs --project desktop`
passes arguments to Playwright; `E2E_FORCE_BUILD=1` rebuilds `out/` first,
`E2E_SKIP_BUILD=1` never builds, `E2E_KEEP_TMP=1` keeps the run's temporary
D1 directories. `npm run e2e:fakes` checks the two fakes on their own with
the official `stripe` client.

CI runs `npm run check`, `npm run build` and `npm run e2e` in
`.github/workflows/site.yml` with no secrets.

### Real Stripe test mode

The code talks to Stripe through the official `stripe` package, so a real
test-mode account works without code changes (this was not exercised by the
automated suite, which has no Stripe account):

1. Put an `sk_test_` key in `.dev.vars` and leave `STRIPE_API_BASE` unset.
2. `npm run preview` (wrangler serves on port 8788 by default).
3. In another terminal: `stripe listen --forward-to localhost:8788/api/stripe/webhook`
   and copy the `whsec_` secret it prints into `STRIPE_WEBHOOK_SECRET`; restart
   the preview.
4. Buy a plan with card 4242 4242 4242 4242. `stripe trigger
   checkout.session.completed` sends synthetic events (they match no order and
   are ignored); refund a real test payment from the dashboard to see
   `charge.refunded` revoke its license.

### Unit tests

`npm run test:unit` (`node --test`) covers the pure logic the browser tests
reach poorly: canonical JSON, license ids, signing and tamper detection, term
dates and the decision guide.

## Configuration

### `config/commerce.json`

The single source for tiers, options, prices and seller facts. The plans page
and the checkout Function both read it; the server always prices an order
from it, never from the client. `npm run check` validates it
(`lib/commerce.ts`, `validateCommerceConfig`).

| Field | Meaning |
|---|---|
| `pricesArePlaceholders` | `true` shows the preview notice and blocks live payments |
| `currency` | lowercase ISO code with two decimals (`usd`) |
| `seller.displayName` / `legalName` / `contactEmail` | `legalName` null blocks live payments |
| `termMonths` | license term |
| `tiers[].options[]` | `id`, `annual` (minor units), and `seats`, `aumBandUsd` or `deployment` |
| `tiers[].quoteAbove` | what needs a quote instead |

### Environment

| Name | Kind | Purpose |
|---|---|---|
| `DB` | D1 binding | database `pineforge-license` |
| `STRIPE_SECRET_KEY` | secret | `sk_test_` or `sk_live_`; the prefix decides the deployment's mode |
| `STRIPE_WEBHOOK_SECRET` | secret | `whsec_` of the webhook endpoint, per mode |
| `LICENSE_SIGNING_KEY` | secret | Ed25519 private JWK with `kid` (JSON string) |
| `RESEND_API_KEY` | secret | Resend API key |
| `TURNSTILE_SECRET` | secret, optional | makes `/api/quote` require a Cloudflare Turnstile token; the quote form does not send one yet, so leave it unset |
| `SITE_URL` | var | absolute base URL used in Stripe return URLs and emails; empty = the request's origin |
| `RESEND_FROM` | var | `PineForge Licensing <enterprise@pineforge.dev>` |
| `LICENSE_NOTIFY_TO` | var | sale notices, quote requests and alerts (`enterprise@pineforge.dev`) |
| `STRIPE_TAX` | var | `"on"` sets `automatic_tax.enabled` (Stripe Tax); default `"off"` |
| `TEST_EMAIL_ALLOWLIST` | var | test mode only: comma-separated addresses or `@domain`s the buyer email may go to; empty (the default) sends none; live mode ignores it |
| `STRIPE_API_BASE` | var, optional | default `https://api.stripe.com` (tests point it at the fake) |
| `RESEND_API_BASE` | var, optional | default `https://api.resend.com` |
| `LICENSE_PUBLIC_KEYS` | var, optional | JSON keyring that REPLACES the bundled one: local development, tests and preview deployments only; never set in production |

### Adding a locale

1. Add it to `i18n/locales.json` (the one list of locales).
2. Add `messages/<locale>.json` with every key of `messages/en.json`
   (`npm run check:i18n` fails on a missing or extra key or a changed
   placeholder).
3. LICENSE quotations stay in English: they live in
   `content/license-quotes.ts` and are never translated.

## Architecture

### Pages (`app/[locale]/`)

| Route | Page |
|---|---|
| `/en/` | what the commercial license covers, and the "Do I need one?" guide (`lib/decide.ts`; readable without JavaScript) |
| `/en/plans/` | the tiers as a price ledger from `config/commerce.json` |
| `/en/checkout/?tier=&option=` | order summary and buyer form; posts to `/api/checkout`, then redirects to Stripe |
| `/en/order/?session_id=` | polls `/api/order` until the license is issued; download, certificate, verify |
| `/en/verify/` | look up a license id, or paste/upload the signed license JSON |
| `/en/faq/`, `/en/terms/`, `/en/contact/` | answers, the draft agreement, the quote form |
| `/certificate/<id>` | printable certificate (a Function) |
| `/.well-known/pineforge-license-keys.json` | the trusted public keyring |

### Functions (`functions/`, shared code in `server/` and `lib/`)

| Endpoint | Does |
|---|---|
| `POST /api/checkout` | validates the plan and buyer, rate-limits per IP, stores a pending order, creates the Checkout Session (idempotency key = order id), returns its URL |
| `POST /api/stripe/webhook` | verifies the signature on the raw body, rejects the other mode's events, handles each event id once, issues or revokes |
| `GET /api/order?session_id=` | order status, public license fields and the signed license JSON |
| `GET /api/verify?id=`, `POST /api/verify` | signature, D1 status, dates and mode |
| `POST /api/quote` | stores the request, then emails `LICENSE_NOTIFY_TO` (reply-to the requester) |
| `GET /certificate/<id>` | the certificate page |

Webhook events handled: `checkout.session.completed` and
`checkout.session.async_payment_succeeded` (issue when `payment_status` is
`paid`), `checkout.session.async_payment_failed`, `checkout.session.expired`,
`charge.refunded` (a full refund revokes the license; a partial refund is
recorded only), `charge.dispute.created` (one alert) and
`charge.dispute.closed` (lost: the license is revoked with reason
`dispute_lost` and an alert goes out; won: recorded only).

Emails: the buyer gets the license (signed JSON attached) and
`LICENSE_NOTIFY_TO` a sale notice. In test mode the buyer email goes only to
addresses on `TEST_EMAIL_ALLOWLIST`; for anyone else it is logged as
skipped, while the order page, the certificate and the sale notice work as
usual. When issuance fails for a paid order (signing key missing or not in
the keyring, a database error), one alert per order goes to
`LICENSE_NOTIFY_TO` and the webhook answers 500, so Stripe retries.

### D1 tables (`migrations/`)

`migrations/0001_init.sql` changed while this site was being built, before
its first deploy (licenses gained `emailed_at` and `email_claimed_at`; orders
gained the `disputed` status). Wrangler applies a migration file only once, so
a database created from an earlier version of it must be deleted and created
again: locally, delete `site/.wrangler/state/v3/d1` and run
`npm run d1:migrate:local`; for a remote database, delete it in the
Cloudflare dashboard (or `npx wrangler d1 delete <name>`), create it again
(`npx wrangler d1 create <name>`, then update its id where it is bound) and
migrate it: `npm run d1:migrate:remote` for the production database
`pineforge-license`, `npx wrangler d1 migrations apply <name> --remote` for a
preview one.

`orders` (pending, paid, refunded, expired, failed, mismatch, disputed), `licenses`
(at most one per order; active or revoked), `stripe_events` (processed event
ids), `quotes`, `email_log` (every email attempt and its result) and
`rate_limits`.

### License format

```json
{
  "license": {
    "v": 1,
    "id": "PFL-XXXX-XXXX-XXXX-XXXX",
    "product": "pineforge-codegen",
    "licensee": { "company": "…", "country": "…" },
    "tier": "team",
    "option": "seats-5",
    "scope": { "seats": 5 },
    "term": { "months": 12, "validFrom": "…", "validUntil": "…" },
    "issuedAt": "…",
    "mode": "test",
    "agreement": { "version": "…", "sha256": "…" },
    "orderRef": "ord_…"
  },
  "signature": { "alg": "Ed25519", "kid": "…", "value": "<base64url>" }
}
```

The signature covers the canonical JSON of `license` (keys sorted at every
depth, no whitespace: `lib/canonical-json.ts`, used by both signing and
verification). License ids carry 80 random bits in Crockford base32. The
buyer's email is never in a license, a certificate or a verify response.

`mode: "test"` marks a license issued from a Stripe test-mode payment. It is
not a commercial license; the certificate, the verify page and the offline
verifier say so, and a live deployment never reports one as valid.

### Keys and rotation

- `keys/license-public-keys.json` is the trusted keyring. It ships the
  production public key `pfl-live-2026-10`; its private key is held by the
  owner and is not in this repository. The build publishes the keyring at
  `/.well-known/pineforge-license-keys.json`.
- `node scripts/generate-signing-key.mjs <kid>` makes a new pair: the private
  JWK goes into the `LICENSE_SIGNING_KEY` secret, the public JWK into the
  keyring. Keep retired public keys in the keyring so the licenses they signed
  still verify.
- `node scripts/verify-license.mjs <license.json> [--keyring <file>]` checks
  a license offline. It exits 0 only for a valid live license within its
  term; a test license or one outside its term exits 3 unless `--allow-test`
  / `--allow-expired` is given; an invalid one exits 1. Revocation is only
  visible online.
- Issuance refuses to sign a test-mode license with a key whose kid is in the
  bundled (production) keyring.

### Revocation

A full refund (`charge.refunded`) marks the order refunded and the license
revoked with reason `refund`; a lost dispute (`charge.dispute.closed`,
status `lost`) marks the order `disputed` (it can never issue afterwards) and
revokes any license with reason `dispute_lost`. Verification then reports
`revoked`, and the certificate shows REVOKED. Public answers and pages give
the reason only as "refund" or a neutral "revoked by the licensor", never a
dispute. Any other revocation is
a manual D1 update of `licenses.status`, `revoked_at` and `revoke_reason`.

### Reconciliation

Paid orders that have no license (issuance failed or was blocked; each one
also raised an alert):

```bash
npx wrangler d1 execute pineforge-license --remote --command "
  SELECT o.id, o.company, o.tier, o.option_id, o.paid_at, o.stripe_payment_intent
  FROM orders o LEFT JOIN licenses l ON l.order_id = o.id
  WHERE o.status = 'paid' AND l.id IS NULL
  ORDER BY o.paid_at"
```

Once the cause is fixed, resend the order's `checkout.session.completed`
event from the Stripe dashboard (Developers, Events) to issue the license,
or refund the payment.

## Security notes

- **Webhook signature.** The raw request body is verified with
  `stripe.webhooks.constructEventAsync` (WebCrypto provider, Stripe's default
  300-second tolerance). A missing or invalid signature gets 400 and changes
  nothing.
- **Mode check.** An event whose `livemode` differs from the deployment's mode
  (decided by the `sk_live_` / `sk_test_` key prefix) is rejected.
- **Idempotency.** Stripe delivers events at least once, so every handler is
  idempotent: an event id is recorded (`stripe_events`) only after it was
  handled, a license is issued at most once per order (`licenses.order_id`
  is unique), the buyer email is claimed before it is sent (a delivery that
  finds a fresh claim held by another answers 500, so Stripe retries), and
  the Checkout Session uses the order id as its idempotency key. Webhook
  bodies over 1 MiB are refused (413) before the signature check.
- **Amount check.** The paid session must match the order by session id and
  `client_reference_id`, and its `amount_subtotal` and currency must equal the
  order's; otherwise the order is marked `mismatch`, an alert is emailed and no
  license is issued.
- **Server-side prices.** The client sends only a tier and option id.
- **Rate limits.** Checkout and quote requests are limited per client (D1
  fixed windows: 30 checkouts per 10 minutes, 10 quotes per 15 minutes),
  keyed by the IPv4 address or the IPv6 /64. Both forms carry a honeypot field
  with a non-semantic name (`pf_hp`), and both endpoints accept only
  `Content-Type: application/json`, which a cross-site HTML form cannot send.
- **Test deployments.** A test-mode deployment emails licenses only to
  `TEST_EMAIL_ALLOWLIST`, so it cannot be used to send mail from
  enterprise@pineforge.dev to arbitrary addresses. Put preview deployments
  behind Cloudflare Access as well, so only the team can reach them.
- **Stripe API version.** The client pins `2026-09-30.endive` (the version
  of the `stripe` package's types); create the webhook endpoints with the same
  API version, so event payloads match what the code expects.
- **Currency.** The amount check compares the session's `amount_subtotal` and
  currency with the order's (USD). If Stripe Adaptive Pricing is turned on,
  buyers may pay in their local currency and the session's amounts change, so
  every such payment would be marked `mismatch`; keep Adaptive Pricing off,
  or extend the check to the original-currency amounts Stripe reports on such
  sessions before turning it on.
- **Keys.** A license is stored only if it verifies against the trusted
  keyring right after signing. A deployment holding a live Stripe key
  ignores `STRIPE_API_BASE`, `RESEND_API_BASE` and `LICENSE_PUBLIC_KEYS`.
- **No PII in licenses.** Licenses, certificates and verification responses
  show the company and country only.
- **Headers.** `public/_headers` denies framing and sets a strict referrer
  policy and `nosniff` for the static pages.

## Deploy plan (documented, not executed)

1. **Pages project.** Create a Cloudflare Pages project `pineforge-license`
   connected to this repository: root directory `site`, build command
   `npm ci && npm run build`, output directory `out`, Node 22. Preview
   deployments for pull requests stay in test mode.
2. **Domain.** Proposed `license.pineforge.dev`; the owner adds the DNS
   record and the custom domain in Pages. Set `SITE_URL` to match.
3. **D1: one database per environment.** `npm run d1:create` creates
   `pineforge-license` for production; put its id into `wrangler.jsonc`
   (`database_id`) and run `npm run d1:migrate:remote`. Create a second
   database (for example `wrangler d1 create pineforge-license-preview`),
   migrate it, and bind it as `DB` for the preview environment (Pages
   settings, or an `env.preview` block in `wrangler.jsonc`, which must then
   repeat `d1_databases` and `vars`). Test-mode orders and licenses never
   share the production database.
4. **Secrets and variables**, per environment, with `wrangler pages secret
   put <NAME> --project-name pineforge-license` (add `--env preview` for
   preview):
   - production: `STRIPE_SECRET_KEY` (live, only after the go-live
     checklist), `STRIPE_WEBHOOK_SECRET` (the live endpoint's),
     `LICENSE_SIGNING_KEY` (the production key `pfl-live-2026-10`, from the
     owner's keychain), `RESEND_API_KEY`. Never `LICENSE_PUBLIC_KEYS`,
     `STRIPE_API_BASE` or `RESEND_API_BASE` (a live deployment ignores them).
   - preview: a Stripe test key and the test endpoint's webhook secret, a
     separate signing key (`node scripts/generate-signing-key.mjs
     pfl-preview-...`) with `LICENSE_PUBLIC_KEYS` set to a keyring holding
     its public key (issuance refuses a license that the trusted keyring
     cannot verify), `RESEND_API_KEY`, and `SITE_URL` set to the preview
     alias below. The `vars` in `wrangler.jsonc` apply to preview
     deployments too, so without this override Stripe return URLs and
     email links would point at the production domain (an empty
     `SITE_URL` falls back to each request's own origin).
5. **Stripe: one webhook endpoint per mode, each on its own deployment.**
   - Live mode: `https://license.pineforge.dev/api/stripe/webhook`.
   - Test mode: the preview branch's stable alias (for example
     `https://<branch>.<project>.pages.dev/api/stripe/webhook` for a
     dedicated `preview` branch), never the production URL: the production
     deployment rejects test-mode events (`livemode_mismatch`).

   Create both with API version `2026-09-30.endive` (the version the client
   pins) and subscribe both to `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `checkout.session.expired`,
   `charge.refunded`, `charge.dispute.created` and `charge.dispute.closed`.
   Keep Adaptive Pricing off (see "Security notes"). Configure invoice settings (the seller's legal name,
   address and tax ids on invoices; Checkout creates an invoice for every
   purchase) and, if Stripe Tax is used, the origin address and
   registrations, then set `STRIPE_TAX` to `"on"`.
6. **Resend.** Verify the sending domain (`pineforge.dev`: the DKIM and SPF
   records Resend lists) so `enterprise@pineforge.dev` can send.

## Go-live checklist

Owner decisions:

- [ ] Real prices per tier in `config/commerce.json`, then
      `pricesArePlaceholders: false`.
- [ ] The selling legal entity in `seller.legalName` (and on Stripe invoices).
- [ ] Counsel's review of `legal/commercial-license-agreement.md`; only after
      it: remove the DRAFT marker, fill every bracketed placeholder, give the
      text a final `Version:` line (without "draft") and add the line
      `Status: final`.
- [ ] The refund policy wording (in the agreement and the FAQ).
- [ ] The Stripe account: business details, payouts, tax settings and
      registrations, invoice template, and customer emails for successful
      payments and invoices turned on (the site tells buyers their invoice
      comes from Stripe by email).
- [ ] The domain `license.pineforge.dev`.

Operations:

- [ ] Pages project; production and preview D1 databases, both migrated;
      production and preview secrets and variables set as above.
- [ ] Stripe live and test webhook endpoints, each on its own deployment,
      on API version `2026-09-30.endive`, with the seven events; their
      secrets stored.
- [ ] Preview deployments behind Cloudflare Access; `TEST_EMAIL_ALLOWLIST`
      set on preview to the team's addresses or domain.
- [ ] Resend domain verified; a test purchase in preview delivers both emails.
- [ ] One end-to-end purchase with a real test-mode key on the preview
      deployment (see "Real Stripe test mode"), including a refund.
- [ ] `npm run build` with the live keys passes the guard only after the
      three conditions above are met.
- [ ] `LICENSE_PUBLIC_KEYS`, `STRIPE_API_BASE` and `RESEND_API_BASE` are
      NOT set in production.
- [ ] When the site opens, update the root `README.md` ("Buying a commercial
      license") and `LEGAL.md`, which today name license.pineforge.dev as plain
      text and say it is not yet online: restore the link and say it takes
      orders.

## Known limits

- A live payment that arrives while live payments are blocked, or a paid
  session whose amount does not match its order, is recorded and alerted
  but issues no license; issuing or refunding it is a manual step (see
  "Reconciliation"; there is no operator script yet).
- A buyer email that Resend does not accept is retried on Stripe's
  redelivery of the event; the sale notice to `LICENSE_NOTIFY_TO` is sent
  once and only logged on failure.
- The certificate page shows the D1 status (active, revoked, expired) and
  the TEST banner; it does not re-check the signature (the verify page
  does).
- `TURNSTILE_SECRET` is accepted by `/api/quote`, but the quote form does
  not render a Turnstile widget yet.
- Not built: a PDF certificate (print the page to PDF), a
  `security.txt`, more locales.
